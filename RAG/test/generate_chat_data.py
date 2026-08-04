#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成模拟聊天数据程序

功能：
1. 生成大量模拟的用户询问历史聊天内容
2. 将数据插入到Milvus知识库
3. 生成markdown文档展示编造的内容
"""

import os
import sys
import time
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any

# 添加项目路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Millvus_base import (
    init_milvus_client,
    DoubaoEmbeddings
)


def generate_chat_history(count: int = 50) -> List[Dict[str, Any]]:
    """
    生成模拟的聊天历史数据
    
    Args:
        count: 生成的聊天记录数量
        
    Returns:
        聊天记录列表
    """
    # 预定义的问题和回答模板
    question_templates = [
        "什么是{topic}？",
        "{topic}有什么作用？",
        "如何使用{topic}？",
        "{topic}的优缺点是什么？",
        "{topic}和{other_topic}有什么区别？",
        "{topic}的最新发展是什么？",
        "{topic}适合哪些场景？",
        "如何学习{topic}？",
        "{topic}的未来发展趋势是什么？",
        "{topic}的常见问题有哪些？"
    ]
    
    topics = [
        "Milvus", "向量数据库", "AI", "机器学习", "深度学习",
        "语义搜索", "推荐系统", "大语言模型", "RAG", "知识库"
    ]
    
    # 生成聊天记录
    chat_history = []
    session_ids = [f"session_{i}" for i in range(10)]
    user_ids = [f"user_{i}" for i in range(20)]
    
    for i in range(count):
        # 随机选择主题
        import random
        topic = random.choice(topics)
        other_topic = random.choice([t for t in topics if t != topic])
        
        # 生成问题
        question_template = random.choice(question_templates)
        question = question_template.format(topic=topic, other_topic=other_topic)
        
        # 生成回答
        if "什么是" in question:
            answer = f"{topic}是一种{random.choice(['先进的', '高效的', '智能的', '实用的'])}技术，用于{random.choice(['存储和检索数据', '处理复杂信息', '提供智能服务', '优化系统性能'])}" \
                     f"。它具有{random.choice(['快速', '可靠', '可扩展', '灵活'])}的特点，适用于{random.choice(['企业级应用', '科研项目', '个人开发', '大规模系统'])}。"
        elif "有什么作用" in question:
            answer = f"{topic}的主要作用包括：{random.choice(['提高数据处理效率', '增强系统智能性', '优化用户体验', '降低运营成本'])}、{random.choice(['支持复杂查询', '提供实时分析', '实现个性化推荐', '保障数据安全'])}，" \
                     f"以及{random.choice(['简化开发流程', '提高决策质量', '加速创新速度', '提升竞争力'])}。"
        elif "如何使用" in question:
            answer = f"使用{topic}的基本步骤包括：首先，{random.choice(['安装并配置环境', '了解基本概念', '准备数据源', '设计系统架构'])}；" \
                     f"然后，{random.choice(['创建实例', '导入数据', '配置参数', '编写代码'])}；" \
                     f"最后，{random.choice(['测试功能', '优化性能', '部署应用', '监控运行状态'])}。"
        else:
            answer = f"关于{topic}，{random.choice(['它是一个重要的技术领域', '有很多值得探索的内容', '需要持续学习和实践', '在未来将发挥更大作用'])}。" \
                     f"{random.choice(['建议深入学习相关文档', '参考实际应用案例', '参与社区讨论', '尝试构建示例项目'])}来更好地理解和应用它。"
        
        # 生成用户消息
        user_message = {
            "session_id": random.choice(session_ids),
            "role_id": "user",
            "user_id": random.choice(user_ids),
            "message_type": "user_message",
            "content": question,
            "timestamp": datetime.now().isoformat(),
            "context_relevance": random.uniform(0.7, 1.0),
            "is_important": random.random() > 0.7,
            "metadata": {
                "source": "simulated",
                "created_by": "chat_generator",
                "tags": [topic.lower(), "question"]
            }
        }
        
        # 生成助手消息
        assistant_message = {
            "session_id": user_message["session_id"],
            "role_id": "assistant",
            "user_id": user_message["user_id"],
            "message_type": "assistant_message",
            "content": answer,
            "timestamp": datetime.now().isoformat(),
            "context_relevance": random.uniform(0.8, 1.0),
            "is_important": random.random() > 0.5,
            "metadata": {
                "source": "simulated",
                "created_by": "chat_generator",
                "tags": [topic.lower(), "answer"]
            }
        }
        
        chat_history.extend([user_message, assistant_message])
    
    return chat_history


def insert_chat_data(chat_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将聊天数据插入到Milvus知识库
    
    Args:
        chat_history: 聊天记录列表
        
    Returns:
        插入结果
    """
    try:
        # 初始化Milvus客户端
        client = init_milvus_client(
            uri="http://localhost:19530",
            token="root:Milvus",
            db_name="LLM_vtuber"
        )
        
        # 初始化嵌入模型
        embedding_model = DoubaoEmbeddings()
        
        # 准备插入数据
        contents = [msg["content"] for msg in chat_history]
        vectors = embedding_model.embed_documents(contents)
        
        insert_data = []
        for i, msg in enumerate(chat_history):
            insert_data.append({
                "session_id": msg["session_id"],
                "role_id": msg["role_id"],
                "user_id": msg["user_id"],
                "message_type": msg["message_type"],
                "content": msg["content"],
                "timestamp": msg["timestamp"],
                "content_vector": vectors[i],
                "context_relevance": msg["context_relevance"],
                "is_important": msg["is_important"],
                "metadata": msg["metadata"]
            })
        
        # 插入数据
        start_time = time.time()
        result = client.insert(
            collection_name="chat_history",
            data=insert_data
        )
        execution_time = time.time() - start_time
        
        # 处理插入结果
        inserted_count = len(insert_data)
        inserted_ids = result.get("ids", []) if isinstance(result, dict) else []
        
        print(f"✅ 成功插入 {inserted_count} 条聊天记录，耗时 {execution_time:.3f} 秒")
        print(f"   生成的ID数量: {len(inserted_ids)}")
        
        return {
            "success": True,
            "inserted_count": inserted_count,
            "inserted_ids": inserted_ids,
            "execution_time": execution_time
        }
        
    except Exception as e:
        error_msg = f"插入聊天数据失败: {str(e)}"
        print(f"❌ {error_msg}")
        return {
            "success": False,
            "error": error_msg
        }


def generate_markdown_report(chat_history: List[Dict[str, Any]], output_file: str = "chat_data_report.md"):
    """
    生成markdown文档展示聊天数据
    
    Args:
        chat_history: 聊天记录列表
        output_file: 输出文件路径
    """
    # 按会话分组
    sessions = {}
    for msg in chat_history:
        session_id = msg["session_id"]
        if session_id not in sessions:
            sessions[session_id] = []
        sessions[session_id].append(msg)
    
    # 生成markdown内容
    markdown_content = f"""# 模拟聊天数据报告

## 概览

- **生成时间**: {datetime.now().isoformat()}
- **总聊天记录数**: {len(chat_history)}
- **总会话数**: {len(sessions)}
- **消息类型分布**:
  - 用户消息: {sum(1 for msg in chat_history if msg["message_type"] == "user_message")}
  - 助手消息: {sum(1 for msg in chat_history if msg["message_type"] == "assistant_message")}

## 详细聊天记录

"""
    
    # 添加每个会话的聊天记录
    for session_id, messages in sessions.items():
        # 按时间排序
        messages.sort(key=lambda x: x["timestamp"])
        
        # 提取会话主题
        topics = set()
        for msg in messages:
            tags = msg.get("metadata", {}).get("tags", [])
            topics.update(tags)
        
        markdown_content += f"### 会话 {session_id}\n"
        markdown_content += f"**主题**: {', '.join(topics)}\n"
        markdown_content += f"**消息数**: {len(messages)}\n"
        markdown_content += "\n"  
        
        for msg in messages:
            role = "用户" if msg["message_type"] == "user_message" else "助手"
            timestamp = msg["timestamp"]
            content = msg["content"]
            is_important = " ⭐" if msg["is_important"] else ""
            
            markdown_content += f"**{role}{is_important}** ({timestamp.split('T')[1].split('.')[0]}):\n"
            markdown_content += f"> {content}\n\n"
        
        markdown_content += "---\n\n"
    
    # 保存markdown文件
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    
    print(f"✅ Markdown报告已生成: {output_file}")


def main():
    """
    主函数
    """
    print("🚀 开始生成模拟聊天数据")
    print("=" * 60)
    
    # 生成聊天数据
    print("1. 生成模拟聊天历史...")
    chat_history = generate_chat_history(count=100)  # 生成100条记录（50个问答对）
    print(f"   生成了 {len(chat_history)} 条聊天记录")
    
    # 插入数据到知识库
    print("\n2. 插入数据到Milvus知识库...")
    insert_result = insert_chat_data(chat_history)
    
    # 生成markdown报告
    print("\n3. 生成markdown报告...")
    report_file = f"chat_data_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    generate_markdown_report(chat_history, report_file)
    
    # 保存数据到JSON文件（用于备份）
    json_file = f"chat_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(chat_history, f, ensure_ascii=False, indent=2)
    print(f"✅ 数据已保存到: {json_file}")
    
    print("\n" + "=" * 60)
    print("🎉 模拟聊天数据生成完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
