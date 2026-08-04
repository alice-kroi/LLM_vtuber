#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成测试消息数据的脚本

根据 message_state_schema.json 中定义的消息数据结构，生成符合要求的测试数据。
"""

import json
import os
import argparse
import uuid
import random
from datetime import datetime, timedelta
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TestMessageDataGenerator:
    """
    测试消息数据生成器
    """
    
    def __init__(self):
        """
        初始化生成器
        """
        self.roles = ["user", "assistant", "system", "tool"]
        self.statuses = ["sent", "received", "processing", "processed", "error"]
        self.user_contents = [
            "你好，能介绍一下自己吗？",
            "今天天气怎么样？",
            "如何学习Python编程？",
            "推荐一部好看的电影",
            "什么是人工智能？",
            "如何保持健康的生活方式？",
            "解释一下量子计算",
            "如何提高英语口语？",
            "推荐一些旅游景点",
            "什么是区块链技术？"
        ]
        self.assistant_contents = [
            "你好！我是一个AI助手，很高兴为你服务。",
            "今天天气晴朗，适合户外活动。",
            "学习Python编程可以从基础语法开始，然后逐步学习面向对象编程。",
            "推荐你看《盗梦空间》，这是一部非常精彩的科幻电影。",
            "人工智能是指让计算机模拟人类智能的技术。",
            "保持健康的生活方式需要均衡饮食、适量运动和充足的睡眠。",
            "量子计算是利用量子力学原理进行计算的一种新型计算方式。",
            "提高英语口语需要多听多说，可以通过看英文电影、听英文歌曲等方式。",
            "推荐你去杭州西湖、北京故宫、上海外滩等旅游景点。",
            "区块链技术是一种分布式账本技术，具有去中心化、不可篡改等特点。"
        ]
        self.system_contents = [
            "系统初始化完成",
            "模型已加载",
            "数据库连接成功",
            "系统更新中",
            "安全检查完成"
        ]
        self.tool_contents = [
            "计算器工具已调用",
            "天气查询工具已调用",
            "翻译工具已调用",
            "搜索工具已调用",
            "地图工具已调用"
        ]
    
    def generate_message(self, index):
        """
        生成单个消息数据
        
        Args:
            index: 消息索引
        
        Returns:
            消息数据字典
        """
        role = random.choice(self.roles)
        
        # 根据角色生成不同的内容
        if role == "user":
            content = random.choice(self.user_contents)
        elif role == "assistant":
            content = random.choice(self.assistant_contents)
        elif role == "system":
            content = random.choice(self.system_contents)
        else:  # tool
            content = random.choice(self.tool_contents)
        
        # 生成时间戳（过去7天内的随机时间）
        now = datetime.now()
        random_days = random.randint(0, 6)
        random_seconds = random.randint(0, 86399)
        timestamp = (now - timedelta(days=random_days, seconds=random_seconds)).isoformat()
        
        # 生成向量（1536维，模拟OpenAI的text-embedding-ada-002模型）
        vector = [random.uniform(-1.0, 1.0) for _ in range(1536)]
        
        # 生成消息数据
        message = {
            "message_id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "status": random.choice(self.statuses),
            "vector": vector
        }
        
        return message
    
    def generate_test_data(self, count):
        """
        生成指定数量的测试数据
        
        Args:
            count: 生成的数据数量
        
        Returns:
            测试数据列表
        """
        messages = []
        for i in range(count):
            message = self.generate_message(i)
            messages.append(message)
            if (i + 1) % 100 == 0:
                logger.info(f"已生成 {i + 1} 条消息数据")
        return messages
    
    def save_test_data(self, messages, output_dir):
        """
        保存测试数据到文件
        
        Args:
            messages: 消息数据列表
            output_dir: 输出目录
        """
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(output_dir, f"test_messages_{timestamp}.json")
        
        # 保存数据
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            logger.info(f"测试数据已保存到: {output_file}")
            return output_file
        except Exception as e:
            logger.error(f"保存测试数据失败: {e}")
            return None

def main():
    """
    主函数
    """
    logger.info("=== 开始生成测试消息数据 ===")
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="生成测试消息数据")
    parser.add_argument('--count', type=int, default=100, help='生成的消息数量')
    parser.add_argument('--output', type=str, default=None, help='输出目录')
    args = parser.parse_args()
    
    # 设置默认输出目录
    if args.output is None:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "LLM", "test", "test_data")
    else:
        output_dir = args.output
    
    # 创建生成器
    generator = TestMessageDataGenerator()
    
    # 生成测试数据
    messages = generator.generate_test_data(args.count)
    
    # 保存测试数据
    output_file = generator.save_test_data(messages, output_dir)
    
    if output_file:
        logger.info(f"成功生成 {args.count} 条测试消息数据")
        logger.info(f"输出文件: {output_file}")
    else:
        logger.error("生成测试数据失败")
    
    logger.info("=== 测试消息数据生成完成 ===")

if __name__ == "__main__":
    main()
