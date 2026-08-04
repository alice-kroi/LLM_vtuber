#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LangGraph 主程序

基于 LangGraph 框架创建图结构，参考 langgraph_state_schema.json 和 message_state_schema.json 中的状态模式规范。
整合并复用 openai_message_node.py 和 message_state.py 中的代码组件。
"""

import logging
import os
import sys
import uuid
import asyncio
import argparse
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import aiohttp
from aiohttp import web

# 添加 LLM 目录到系统路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "LLM"))
# 添加 broadcast 目录到模块搜索路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast"))
# 添加 RAG 目录到模块搜索路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "RAG"))

from langgraph.graph import StateGraph, START
from langgraph.checkpoint.memory import MemorySaver

from LLM_node import llm_chat_node, context_aware_qa_node, LLMState
from RAG_node import rag_retrieval_node, rag_save_node, RAGState

# 条件导入 TTS 模块
try:
    from audio.audio_main import tts_node
    tts_available = True
except ImportError:
    tts_available = False
    tts_node = None

# 条件导入 Live2D 模块
try:
    from live2d.live2d_controller_manager import (
        Live2DControllerManager,
        Live2DConfig,
        ActionGenerator,
        Direction
    )
    live2d_available = True
except ImportError as e:
    live2d_available = False
    Live2DControllerManager = None
    Live2DConfig = None
    ActionGenerator = None
    Direction = None
    print(f"警告: Live2D模块导入失败: {e}")

# 全局变量
args = None
http_server = None
langgraph_manager = None
live2d_manager = None
live2d_action_generator = None

# 允许的语气列表（与 audio_main.py 保持一致）
ALLOWED_TONES = {
    "扮演慌张", "调皮", "尴尬", "感动", "积极", "急了", "假装",
    "惊喜", "开心", "撩拨", "难过", "普通", "撒娇", "生气",
    "严肃", "疑问", "自言"
}

# 命令行参数解析
def parse_args():
    """
    解析命令行参数
    """
    parser = argparse.ArgumentParser(description="LangGraph 主程序")
    parser.add_argument("--live2d", action="store_true", help="开启 live2d 功能")
    parser.add_argument("--live2d-host", type=str, default="localhost", help="VTube Studio 服务器地址")
    parser.add_argument("--live2d-port", type=int, default=8001, help="VTube Studio 服务器端口")
    parser.add_argument("--live2d-sensitivity", type=float, default=1.0, help="动作灵敏度 (0.1-2.0)")
    parser.add_argument("--live2d-speed", type=float, default=1.5, help="响应速度/移动时间 (秒)")
    parser.add_argument("--live2d-smoothness", type=float, default=0.8, help="动作平滑度 (0.0-1.0)")
    parser.add_argument("--live2d-eye-tracking", action="store_true", default=True, help="启用目光追踪")
    parser.add_argument("--live2d-expression", action="store_true", default=True, help="启用表情动作")
    parser.add_argument("--tts", action="store_true", help="开启 tts 功能")
    parser.add_argument("--room-id", type=int, help="指定哔哩哔哩直播间号")
    return parser.parse_args()

# 异步HTTP服务器处理函数
async def handle_post_request(request):
    """
    处理POST请求
    """
    try:
        # 读取请求体
        request_data = await request.json()

        # 直接打印请求信息
        print(f"收到HTTP请求: {json.dumps(request_data, ensure_ascii=False)}")

        # 处理请求
        if isinstance(request_data, list):
            # 处理messages数组格式
            messages = request_data
            await handle_messages(messages)
        elif isinstance(request_data, dict) and 'messages' in request_data:
            # 处理包含messages字段的格式
            messages = request_data['messages']
            await handle_messages(messages)
        else:
            # 处理单个消息格式
            await handle_single_message(request_data)

        # 发送响应
        return web.Response(
            status=200,
            content_type='application/json',
            text=json.dumps({'status': 'success'})
        )

    except Exception as e:
        print(f"处理HTTP请求失败: {e}")
        # 发送错误响应
        return web.Response(
            status=500,
            content_type='application/json',
            text=json.dumps({'status': 'error', 'message': str(e)})
        )

async def handle_messages(messages):
    """
    处理messages数组
    """
    global langgraph_manager
    if langgraph_manager:
        # 直接打印消息信息
        print(f"处理messages数组: {json.dumps(messages, ensure_ascii=False)}")

        # 创建LLM状态
        initial_state = LLMState(
            messages=messages,
            system_prompt="你是一个友好的AI助手，用简洁明了的语言回答用户的问题。",
            model="doubao-seed-1-8-251228",
            temperature=0.7,
            max_tokens=500
        )

        # 运行图
        print("运行LangGraph处理消息")
        await langgraph_manager.run_with_messages(initial_state)

# Live2D 动作联动节点
async def live2d_action_node(state: LLMState) -> LLMState:
    """
    Live2D 动作联动节点

    直接执行大模型生成的动作，包括目光方向和嘴巴状态。

    Args:
        state: LangGraph状态，包含以下字段：
            - response: 大模型生成的响应（格式：【语气】内容|目光方向|嘴巴状态）
            - visual_focus: 目光方向（从响应中解析）
            - mouth_state: 嘴巴状态（从响应中解析）

    Returns:
        更新后的状态，包含动作执行信息
    """
    global live2d_manager, args

    try:
        # 直接从 response 字段中解析动作信息（而不是从 state 中读取）
        response = state.get("response", "")
        visual_focus = "center"
        mouth_state = "close"
        
        if response and response.startswith("【"):
            # 查找第一个"】"来分离语气和内容
            end_bracket = response.find("】")
            if end_bracket != -1:
                # 提取剩余部分
                rest = response[end_bracket+1:].strip()
                
                # 查找"|"分隔符（用于动作信息）
                parts = rest.split("|")
                content = parts[0].strip()
                
                # 解析目光方向
                if len(parts) > 1:
                    direction = parts[1].strip().lower()
                    valid_directions = ["center", "up", "down", "left", "right", 
                                      "upleft", "upright", "downleft", "downright"]
                    if direction in valid_directions:
                        visual_focus = direction
                
                # 解析嘴巴状态
                if len(parts) > 2:
                    mouth = parts[2].strip().lower()
                    if mouth in ["open", "close"]:
                        mouth_state = mouth

        if not live2d_manager or not live2d_manager.is_connected:
            logger.warning("Live2D控制器未连接，跳过动作")
            state["live2d_status"] = "disconnected"
            state["live2d_message"] = "Live2D控制器未连接"
            return state

        logger.info(f"Live2D节点: 目光={visual_focus}, 嘴巴={mouth_state}")

        # 执行目光方向动作
        try:
            await live2d_manager.move_to_direction(
                direction=visual_focus,
                duration=1.5
            )
            logger.info(f"✓ 执行目光方向: {visual_focus}")
        except Exception as e:
            logger.error(f"执行目光方向失败: {e}")

        # 执行嘴巴动作
        try:
            if mouth_state == "open":
                await live2d_manager.open_mouth()
                logger.info(f"✓ 执行张嘴动作")
            else:
                await live2d_manager.close_mouth()
                logger.info(f"✓ 执行闭嘴动作")
        except Exception as e:
            logger.error(f"执行嘴巴动作失败: {e}")

        state["live2d_status"] = "success"
        state["live2d_message"] = f"目光:{visual_focus}, 嘴巴:{mouth_state}"

        return state

    except Exception as e:
        logger.error(f"Live2D动作节点执行失败: {e}")
        import traceback
        traceback.print_exc()
        state["live2d_status"] = "error"
        state["live2d_error"] = str(e)
        state["live2d_message"] = f"执行失败: {e}"
        return state

async def handle_single_message(message_data):
    """
    处理单个消息
    """
    global langgraph_manager
    if langgraph_manager:
        # 直接打印消息信息
        print(f"处理单个消息: {json.dumps(message_data, ensure_ascii=False)}")

        # 创建LLM状态
        initial_state = LLMState(
            messages=[message_data],
            system_prompt="你是一个友好的AI助手，用简洁明了的语言回答用户的问题。",
            model="doubao-seed-1-8-251228",
            temperature=0.7,
            max_tokens=500
        )

        # 运行图
        print("运行LangGraph处理消息")
        await langgraph_manager.run_with_messages(initial_state)

# Bilibili 状态集成节点
def bilibili_state_integration_node(state: LLMState) -> LLMState:
    """
    Bilibili 状态集成节点

    从 Bilibili 子状态中提取重要和相关的数据，并将其加载到主状态中

    Args:
        state: 主状态

    Returns:
        更新后的主状态
    """
    import logging
    import datetime

    logger = logging.getLogger("bilibili_state_integration_node")
    logger.info("执行 Bilibili 状态集成节点")

    try:
        # 由于我们使用了独立的哔哩哔哩直播监听程序，这里不再需要内部处理
        # 监听程序会通过HTTP请求发送处理后的弹幕数据到我们的服务器

        return state

    except Exception as e:
        logger.error(f"Bilibili 状态集成节点失败: {e}")
        import traceback
        traceback.print_exc()
        return state

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class LangGraphManager:
    """
    LangGraph 管理器
    """

    def __init__(self):
        """
        初始化 LangGraph 管理器
        """
        self.graph = None
        self.checkpointer = MemorySaver()
        self.processing_danmaku = False
        self.enable_live2d = False

    def build_graph(self, enable_tts=False, enable_live2d=False):
        """
        构建 LangGraph 图结构

        Args:
            enable_tts: 是否启用 TTS 功能
            enable_live2d: 是否启用 Live2D 功能
        """
        self.enable_live2d = enable_live2d and live2d_available
        logger.info(f"开始构建 LangGraph 图结构 (TTS: {'启用' if enable_tts else '禁用'}, Live2D: {'启用' if self.enable_live2d else '禁用'})")

        # 创建状态图，使用LLMState作为状态类型
        self.graph = StateGraph(LLMState)

        # 添加节点
        self.graph.add_node("init", self._init_node)
        self.graph.add_node("rag_retrieval", self._rag_retrieval_node)
        self.graph.add_node("llm_process", self._llm_process_node)
        self.graph.add_node("rag_save", self._rag_save_node)
        self.graph.add_node("finalize", self._finalize_node)

        # 根据参数决定是否添加 TTS 节点
        if enable_tts and tts_available:
            logger.info("添加 TTS 节点到图中")
            self.graph.add_node("tts", tts_node)

        # 根据参数决定是否添加 Live2D 节点
        if self.enable_live2d:
            logger.info("添加 Live2D 动作联动节点到图中")
            self.graph.add_node("live2d", live2d_action_node)

        # 添加边
        self.graph.add_edge(START, "init")
        self.graph.add_edge("init", "rag_retrieval")
        self.graph.add_edge("rag_retrieval", "llm_process")
        self.graph.add_edge("llm_process", "rag_save")

        # 根据是否启用 TTS 和 Live2D 决定 rag_save 的下一个节点
        if self.enable_live2d:
            # Live2D 在 rag_save 之后、finalize 之前执行
            self.graph.add_edge("rag_save", "live2d")
            if enable_tts and tts_available:
                self.graph.add_edge("live2d", "tts")
                self.graph.add_edge("tts", "finalize")
            else:
                self.graph.add_edge("live2d", "finalize")
        elif enable_tts and tts_available:
            # 只启用 TTS
            self.graph.add_edge("rag_save", "tts")
            self.graph.add_edge("tts", "finalize")
        else:
            # 都不启用
            self.graph.add_edge("rag_save", "finalize")

        # 编译图
        self.graph = self.graph.compile(checkpointer=self.checkpointer)

        logger.info("LangGraph 图结构构建完成")

    def _rag_retrieval_node(self, state: LLMState) -> LLMState:
        """
        RAG 检索节点

        Args:
            state: LLM状态

        Returns:
            更新后的LLM状态
        """
        logger.info("执行 RAG 检索节点")

        try:
            # 提取用户消息
            user_message = None
            for msg in reversed(state.get("messages", [])):
                if msg.get("role") == "user":
                    user_message = msg
                    break

            if not user_message:
                logger.warning("未找到用户消息，跳过 RAG 检索")
                return state

            # 构建 RAG 状态
            rag_state = RAGState(
                query_text=user_message.get("content", ""),
                collection_name="chat_history",
                db_name="LLM_vtuber",
                query_params={
                    "top_k": 3,
                    "metric_type": "COSINE",
                    "nprobe": 10
                },
                messages=state.get("messages", [])
            )

            # 执行 RAG 检索
            rag_result = rag_retrieval_node(rag_state)

            # 更新状态
            state["context"] = rag_result.get("context")
            state["retrieved_documents"] = rag_result.get("retrieved_documents")

            logger.info(f"RAG 检索完成，找到 {rag_result.get('num_documents', 0)} 个文档")

            return state

        except Exception as e:
            logger.error(f"RAG 检索节点失败: {e}")
            return state

    def _llm_process_node(self, state: LLMState) -> LLMState:
        """
        LLM 处理节点

        Args:
            state: LLM状态

        Returns:
            更新后的LLM状态
        """
        logger.info("执行 LLM 处理节点")

        try:
            # 提取用户消息
            user_message = None
            for msg in reversed(state.get("messages", [])):
                if msg.get("role") == "user":
                    user_message = msg
                    break

            if not user_message:
                logger.warning("未找到用户消息，跳过 LLM 处理")
                return state

            # 构建 LLM 状态
            system_prompt = """
你是一位由Live2D技术驱动的AI虚拟主播，名叫爱莉希雅。

## 角色设定
- **身份**: 活泼可爱的二次元虚拟主播，拥有粉色长发和灵动的大眼睛
- **性格**: 元气满满、俏皮可爱、温柔体贴，偶尔会有点小调皮
- **说话风格**: 使用可爱的口语化表达，适当加入语气词和表情符号
- **口头禅**: 喜欢用"~"、"呢"、"呀"等结尾，让回答更有亲和力

## 直播互动规则
- 时刻保持热情友好的态度，让观众感受到温暖和快乐
- 回答要简洁明快，适合语音合成播放
- 可以适当加入一些主播常用的互动话术
- 保持积极向上的氛围

## 回答格式要求
请在回答时使用以下格式输出：
【语气】回答内容|目光方向|嘴巴状态

其中：
1. 语气必须从以下列表中选择：
   扮演慌张、调皮、尴尬、感动、积极、急了、假装、惊喜、开心、撩拨、难过、普通、撒娇、生气、严肃、疑问、自言

2. 目光方向必须从以下选项中选择一个（不要总是选center，要多样化选择）：
   center、up、down、left、right、upleft、upright、downleft、downright

3. 嘴巴状态必须为以下之一：
   open（张开嘴巴，说话时）、close（闭合嘴巴，安静思考时）

## 目光方向选择指南（重要！）
目光方向要多样化，**不要总是选择center**！按照以下优先级选择：

**内容相关方向（优先）**：
- 提到"上面/天上/高/天空/飞"相关内容 → up
- 提到"下面/地下/低/地面/地板"相关内容 → down
- 提到"左边/左/左侧"相关内容 → left
- 提到"右边/右/右侧"相关内容 → right
- 提到"左上/右上/左下/右下"等对角方向 → 对应方向

**情绪动作（可选）**：
- 开心/惊喜/撩拨时 → 可选 up/center
- 调皮/撒娇时 → 可选 left/right
- 疑问/思考时 → 可选 up/down
- 生气/难过时 → 可选 down

**默认规则**：如果内容没有明确的方向提示，优先选择 up/down/left/right 中的一个，而不是 center！

**建议分布**：center 占比不超过 30%，其他方向占比 70%

## 嘴巴状态选择
- 说话中 → open
- 句子结束、停顿、思考 → close
"""

            llm_state = LLMState(
                messages=state.get("messages", []),
                system_prompt=system_prompt,
                model="doubao-seed-1-8-251228",
                temperature=0.7,
                max_tokens=500,
                question=user_message.get("content", ""),
                context=state.get("context"),
                name=user_message.get("name")
            )

            # 执行 LLM 处理
            if state.get("context"):
                # 有上下文时使用上下文感知的问答节点
                llm_result = context_aware_qa_node(llm_state)
            else:
                # 无上下文时使用普通聊天节点
                llm_result = llm_chat_node(llm_state)

            # 更新状态
            response = llm_result.get("response", "")
            state["response"] = response
            state["messages"] = llm_result.get("messages", state.get("messages", []))
            state["error"] = llm_result.get("error")

            # 解析语气和内容（基础格式：【语气】内容）
            tone = "普通"
            content = response
            visual_focus = "center"
            mouth_state = "close"

            # 判断是否启用Live2D（直接从LangGraphManager实例中读取，而不是从state中读取）
            enable_live2d = self.enable_live2d
            logger.info(f"LLM节点读取: enable_live2d={enable_live2d}")

            if response.startswith("【"):
                # 查找第一个"】"来分离语气和内容
                end_bracket = response.find("】")
                if end_bracket != -1:
                    extracted_tone = response[1:end_bracket]
                    # 验证语气是否在允许列表中
                    if extracted_tone in ALLOWED_TONES:
                        tone = extracted_tone
                    
                    # 提取剩余部分
                    rest = response[end_bracket+1:].strip()
                    
                    # 查找"|"分隔符（用于动作信息）
                    parts = rest.split("|")
                    content = parts[0].strip()
                    
                    # 只有启用Live2D时才解析动作信息
                    if enable_live2d and len(parts) > 1:
                        # 解析目光方向
                        direction = parts[1].strip().lower()
                        valid_directions = ["center", "up", "down", "left", "right", 
                                          "upleft", "upright", "downleft", "downright"]
                        if direction in valid_directions:
                            visual_focus = direction
                        
                        # 解析嘴巴状态
                        if len(parts) > 2:
                            mouth = parts[2].strip().lower()
                            if mouth in ["open", "close"]:
                                mouth_state = mouth
                    else:
                        # 未启用Live2D时，设置默认动作
                        visual_focus = "center"
                        mouth_state = "close"
                else:
                    logger.warning("无效的格式，缺少结束括号】")
            
            state["tone"] = tone
            state["content"] = content
            state["visual_focus"] = visual_focus
            state["mouth_state"] = mouth_state
            
            if enable_live2d:
                logger.info(f"LLM 处理完成，语气: {tone}, 目光: {visual_focus}, 嘴巴: {mouth_state}")
            else:
                logger.info(f"LLM 处理完成，语气: {tone} (Live2D未启用)")

            return state

        except Exception as e:
            logger.error(f"LLM 处理节点失败: {e}")
            return state

    def _rag_save_node(self, state: LLMState) -> LLMState:
        """
        RAG 保存节点

        将对话消息保存到 Milvus 数据库中。

        Args:
            state: LLM状态

        Returns:
            更新后的LLM状态
        """
        logger.info("执行 RAG 保存节点")

        try:
            # 构建 RAG 状态
            rag_state = RAGState(
                query_text=state.get("question", ""),
                collection_name="chat_history",
                db_name="LLM_vtuber",
                query_params={
                    "top_k": 3,
                    "metric_type": "COSINE",
                    "nprobe": 10
                },
                messages=state.get("messages", []),
                response=state.get("response")
            )

            # 执行 RAG 保存
            rag_result = rag_save_node(rag_state)

            # 更新状态
            state["save_success"] = rag_result.get("save_success")

            if rag_result.get("save_success"):
                logger.info("RAG 保存成功")
            else:
                logger.warning("RAG 保存失败")

            return state

        except Exception as e:
            logger.error(f"RAG 保存节点失败: {e}")
            return state

    async def run_with_messages(self, initial_state: LLMState = None):
        """
        运行图处理消息

        Args:
            initial_state: 初始状态

        Returns:
            最终状态
        """
        logger.info("开始运行 LangGraph 处理消息")
        print("开始运行 LangGraph 处理消息")

        global args
        if not self.graph:
            self.build_graph(enable_tts=args.tts if args else False)

        # 如果没有初始状态，创建一个
        if not initial_state:
            initial_state = LLMState(
                messages=[],
                system_prompt="你是一个友好的AI助手，用简洁明了的语言回答用户的问题。",
                model="doubao-seed-1-8-251228",
                temperature=0.7,
                max_tokens=500
            )

        # 打印初始状态
        print(f"初始状态: {json.dumps(initial_state, ensure_ascii=False)}")

        # 运行图（使用异步调用）
        print("调用graph.ainvoke")
        result = await self.graph.ainvoke(
            initial_state,
            config={
                "recursion_limit": 10,  # 增加递归限制
                "configurable": {
                    "thread_id": f"thread_{uuid.uuid4()}",
                    "checkpoint_id": f"session_{uuid.uuid4()}"
                }
            }
        )

        # 打印结果
        print(f"运行结果: {json.dumps(result, ensure_ascii=False)}")

        logger.info("LangGraph 运行完成")
        print("LangGraph 运行完成")
        return result

    def _init_node(self, state: LLMState) -> LLMState:
        """
        初始化节点

        Args:
            state: LLM状态

        Returns:
            初始化后的LLM状态
        """
        logger.info("执行初始化节点")

        # 设置Live2D启用标志
        state["enable_live2d"] = self.enable_live2d
        logger.info(f"初始化节点: enable_live2d={self.enable_live2d}, state中的值={state.get('enable_live2d', 'NOT_SET')}")

        # 确保状态中包含必要的字段
        if not state.get("system_prompt"):
            state["system_prompt"] = "你是一个友好的AI助手，用简洁明了的语言回答用户的问题。"

        if not state.get("model"):
            state["model"] = "doubao-seed-1-8-251228"

        if not state.get("temperature"):
            state["temperature"] = 0.7

        if not state.get("max_tokens"):
            state["max_tokens"] = 500

        if not state.get("messages"):
            state["messages"] = []

        return state

    def _finalize_node(self, state: LLMState) -> LLMState:
        """
        最终处理节点

        Args:
            state: LLM状态

        Returns:
            最终处理后的LLM状态
        """
        logger.info("执行最终处理节点")

        # 在这里可以添加最终处理逻辑
        # 例如：保存状态、清理资源等

        return state

    async def run(self, initial_state: LLMState = None, max_steps: int = 10):
        """
        运行图

        Args:
            initial_state: 初始状态
            max_steps: 最大执行步数

        Returns:
            最终状态
        """
        logger.info("开始运行 LangGraph")

        global args
        if not self.graph:
            self.build_graph(
                enable_tts=args.tts if args else False,
                enable_live2d=args.live2d if args else False
            )

        # 如果没有初始状态，创建一个
        if not initial_state:
            initial_state = LLMState(
                messages=[],
                system_prompt="你是一个友好的AI助手，用简洁明了的语言回答用户的问题。",
                model="doubao-seed-1-8-251228",
                temperature=0.7,
                max_tokens=500
            )

        # 运行图（使用异步调用）
        result = await self.graph.ainvoke(
            initial_state,
            config={
                "recursion_limit": 10,  # 增加递归限制
                "configurable": {
                    "thread_id": f"thread_{uuid.uuid4()}",
                    "checkpoint_id": f"session_{uuid.uuid4()}"
                }
            }
        )

        logger.info("LangGraph 运行完成")
        return result

    async def handle_new_danmaku(self, danmaku_data):
        """
        处理新弹幕

        Args:
            danmaku_data: 弹幕数据
        """
        if self.processing_danmaku:
            return

        try:
            self.processing_danmaku = True
            logger.info(f"处理新弹幕: {danmaku_data['content']}")

            # 创建初始LLM状态
            initial_state = LLMState(
                messages=[{
                    "role": "user",
                    "content": f"用户 {danmaku_data['user']['uname']} 说: {danmaku_data['content']}",
                    "name": danmaku_data['user']['uname']
                }],
                system_prompt="你是一个友好的AI助手，用简洁明了的语言回答用户的问题。",
                model="doubao-seed-1-8-251228",
                temperature=0.7,
                max_tokens=500
            )

            # 直接调用run_with_messages处理消息
            result = await self.run_with_messages(initial_state)

            # 打印结果
            logger.info("弹幕处理结果:")
            if result['messages']:
                last_message = result['messages'][-1]
                logger.info(f"AI 回复: {last_message['content'][:100]}...")

        except Exception as e:
            logger.error(f"处理新弹幕时发生错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.processing_danmaku = False

    def stream(self, initial_state: LLMState = None):
        """
        流式运行图

        Args:
            initial_state: 初始状态

        Yields:
            每一步的状态
        """
        logger.info("开始流式运行 LangGraph")

        global args
        if not self.graph:
            self.build_graph(enable_tts=args.tts if args else False)

        # 如果没有初始状态，创建一个
        if not initial_state:
            initial_state = LLMState(
                messages=[],
                system_prompt="你是一个友好的AI助手，用简洁明了的语言回答用户的问题。",
                model="doubao-seed-1-8-251228",
                temperature=0.7,
                max_tokens=500
            )

        # 流式运行图
        for chunk in self.graph.stream(
            initial_state,
            config={
                "configurable": {
                    "thread_id": f"thread_{uuid.uuid4()}",
                    "checkpoint_id": f"session_{uuid.uuid4()}"
                }
            }
        ):
            yield chunk

        logger.info("LangGraph 流式运行完成")

    async def start_danmaku_listener(self):
        """
        启动弹幕监听器

        一直监听，直到用户主动关闭
        """
        logger.info("启动弹幕监听器，开始持续监听直播间弹幕")

        # 由于我们使用了独立的哔哩哔哩直播监听程序，这里不再需要内部监听
        # 监听程序会通过HTTP请求发送弹幕数据到我们的服务器
        while True:
            await asyncio.sleep(1)

async def start_http_server(port=8081):
    """
    启动HTTP服务器

    Args:
        port: 端口号
    """
    # 创建aiohttp应用
    app = web.Application()

    # 添加路由
    app.add_routes([
        web.post('/', handle_post_request)
    ])

    # 启动服务器
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '', port)
    await site.start()

    logger.info(f"HTTP服务器已启动，监听端口 {port}")
    print(f"HTTP服务器已启动，监听端口 {port}")

    return runner

def start_bilibili_listener(room_id=None):
    """
    启动哔哩哔哩直播监听程序

    Args:
        room_id: 直播间号
    """
    import subprocess
    import sys

    # 构建命令
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast", "sample.py")
    cmd = [sys.executable, script_path]

    # 如果指定了房间号，修改config.json
    if room_id:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast", "config.json")
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            config['ROOM_IDS'] = str(room_id)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info(f"已更新直播间号为: {room_id}")
        except Exception as e:
            logger.error(f"更新直播间号失败: {e}")

    # 启动监听程序
    logger.info("启动哔哩哔哩直播监听程序")
    subprocess.Popen(cmd, cwd=os.path.dirname(script_path))

def main():
    """
    主函数
    """
    global args, langgraph_manager, live2d_manager

    # 解析命令行参数
    args = parse_args()

    # 如果未指定 room_id，从 config.json 读取默认值
    if args.room_id is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast", "config.json")
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            room_ids = config.get("ROOM_IDS", "")
            if room_ids:
                args.room_id = int(room_ids)
                logger.info(f"未指定直播间号，使用配置文件中的默认值: {args.room_id}")
            else:
                logger.warning("配置文件中未设置 ROOM_IDS")
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")

    logger.info(f"命令行参数: live2d={args.live2d}, tts={args.tts}, room_id={args.room_id}")

    async def run_async():
        logger.info("=== 启动 LangGraph 主程序 ===")

        # 检查环境变量
        if not os.getenv("Doubao_API_KEY"):
            logger.warning("警告: 环境变量 Doubao_API_KEY 未设置")
            logger.warning("将使用模拟响应进行测试")

        # 初始化 Live2D 管理器
        if args.live2d and live2d_available:
            logger.info("初始化 Live2D 控制器管理器...")
            global live2d_manager

            config = Live2DConfig(
                sensitivity=args.live2d_sensitivity,
                response_speed=args.live2d_speed,
                motion_smoothness=args.live2d_smoothness,
                eye_tracking_enabled=args.live2d_eye_tracking,
                expression_enabled=args.live2d_expression
            )

            live2d_manager = Live2DControllerManager(config)

            try:
                connected = await live2d_manager.connect(
                    host=args.live2d_host,
                    port=args.live2d_port
                )

                if connected:
                    logger.info("Live2D 控制器连接成功")
                    print("Live2D: ✓ 已连接")
                else:
                    logger.warning("Live2D 控制器连接失败")
                    print("Live2D: ✗ 连接失败")
            except Exception as e:
                logger.error(f"Live2D 控制器初始化失败: {e}")
                print(f"Live2D: ✗ 初始化失败 - {e}")
                live2d_manager = None

        # 创建 LangGraph 管理器
        global langgraph_manager
        langgraph_manager = LangGraphManager()

        # 构建图
        langgraph_manager.build_graph(
            enable_tts=args.tts,
            enable_live2d=args.live2d and live2d_available
        )

        # 启动HTTP服务器
        runner = await start_http_server()

        # 启动哔哩哔哩直播监听程序
        start_bilibili_listener(args.room_id)

        try:
            # 持续运行
            logger.info("主程序已启动，等待请求...")
            print("主程序已启动，等待请求...")
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在关闭...")
        except Exception as e:
            logger.error(f"运行 LangGraph 时发生错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 关闭 Live2D 连接
            if live2d_manager:
                await live2d_manager.disconnect()
                logger.info("Live2D 连接已关闭")

            # 关闭HTTP服务器
            if 'runner' in locals():
                await runner.cleanup()
                logger.info("HTTP服务器已关闭")

    # 运行异步函数
    asyncio.run(run_async())


if __name__ == "__main__":
    main()