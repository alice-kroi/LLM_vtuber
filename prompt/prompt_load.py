#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提示词模板加载程序
用于加载JSON格式的提示词模板并支持变量渲染
"""

import json
import os
from typing import Dict, Any, Optional


class PromptTemplateLoader:
    """
    提示词模板加载器类
    用于加载、解析和渲染JSON格式的提示词模板
    """
    
    def __init__(self, template_file: Optional[str] = None):
        """
        初始化模板加载器
        
        Args:
            template_file: 模板文件路径，如果为None则需要手动加载
        """
        self.template_file = template_file
        self.templates: Dict[str, Dict[str, Any]] = {}
        
        if template_file:
            self.load_templates()
    
    def load_templates(self) -> None:
        """
        从JSON文件加载模板
        
        Raises:
            FileNotFoundError: 模板文件不存在
            json.JSONDecodeError: JSON格式错误
        """
        if not os.path.exists(self.template_file):
            raise FileNotFoundError(f"模板文件不存在: {self.template_file}")
        
        with open(self.template_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # 支持单个模板和模板列表两种格式
        if isinstance(data, list):
            for template in data:
                self._validate_template(template)
                self.templates[template['template_id']] = template
        else:
            self._validate_template(data)
            self.templates[data['template_id']] = data
    
    def _validate_template(self, template: Dict[str, Any]) -> None:
        """
        验证模板结构是否完整
        
        Args:
            template: 模板字典
            
        Raises:
            ValueError: 模板结构不完整或无效
        """
        required_fields = ['template_id', 'name', 'description', 'content', 'variables']
        for field in required_fields:
            if field not in template:
                raise ValueError(f"模板缺少必要字段: {field}")
        
        if not isinstance(template['variables'], list):
            raise ValueError("'variables'字段必须是列表类型")
    
    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        根据ID获取模板
        
        Args:
            template_id: 模板ID
            
        Returns:
            模板字典，如果不存在则返回None
        """
        return self.templates.get(template_id)
    
    def render_template(self, template_id: str, **variables: str) -> str:
        """
        渲染模板，替换变量
        
        Args:
            template_id: 模板ID
            **variables: 变量键值对
            
        Returns:
            渲染后的提示词
            
        Raises:
            ValueError: 模板不存在或变量不足
        """
        template = self.get_template(template_id)
        if not template:
            raise ValueError(f"模板不存在: {template_id}")
        
        # 检查必需变量
        missing_vars = [var for var in template['variables'] if var not in variables]
        if missing_vars:
            raise ValueError(f"缺少必要变量: {', '.join(missing_vars)}")
        
        # 渲染模板
        content = template['content']
        for var_name, var_value in variables.items():
            placeholder = f"{{{var_name}}}"
            content = content.replace(placeholder, var_value)
        
        return content
    
    def list_templates(self) -> Dict[str, str]:
        """
        列出所有可用模板
        
        Returns:
            模板ID和名称的字典
        """
        return {template_id: template['name'] for template_id, template in self.templates.items()}
    
    def add_template(self, template: Dict[str, Any]) -> None:
        """
        添加新模板
        
        Args:
            template: 模板字典
        """
        self._validate_template(template)
        self.templates[template['template_id']] = template
    
    def save_templates(self, output_file: Optional[str] = None) -> None:
        """
        保存模板到JSON文件
        
        Args:
            output_file: 输出文件路径，如果为None则使用加载时的文件路径
        """
        file_path = output_file or self.template_file
        if not file_path:
            raise ValueError("未指定输出文件路径")
        
        # 转换为列表格式保存
        templates_list = list(self.templates.values())
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(templates_list, f, ensure_ascii=False, indent=2)


def example_usage():
    """
    示例用法
    """
    print("=== 提示词模板加载器示例 ===")
    
    # 创建示例JSON文件内容
    sample_json = {
        "template_id": "character_vtuber_1",
        "name": "虚拟主播角色设定",
        "description": "用于生成虚拟主播的角色设定，包括性格、外貌、背景等",
        "content": "你是一个名为{character_name}的虚拟主播，你的性格是{character_personality}，外貌特点是{character_appearance}，背景故事是{character_backstory}。你的直播风格是{streaming_style}。请以这个角色的身份回应用户的问题。",
        "author": "LLM_vtuber",
        "created_at": "2026-02-02T10:00:00",
        "updated_at": "2026-02-02T10:00:00",
        "tags": ["character", "vtuber", "roleplay"],
        "variables": [
            "character_name",
            "character_personality",
            "character_appearance",
            "character_backstory",
            "streaming_style"
        ]
    }
    
    # 保存示例到文件
    example_file = "example_template.json"
    with open(example_file, 'w', encoding='utf-8') as f:
        json.dump(sample_json, f, ensure_ascii=False, indent=2)
    
    print(f"已创建示例模板文件: {example_file}")
    
    # 加载模板
    loader = PromptTemplateLoader(example_file)
    
    # 列出模板
    print("\n可用模板:")
    for template_id, name in loader.list_templates().items():
        print(f"- {template_id}: {name}")
    
    # 渲染模板
    try:
        rendered = loader.render_template(
            "character_vtuber_1",
            character_name="小初音",
            character_personality="活泼可爱、元气满满",
            character_appearance="蓝绿色双马尾，大眼睛，穿着水手服",
            character_backstory="来自未来的虚拟歌姬，穿越时空来到直播间与粉丝互动",
            streaming_style="轻松愉快，喜欢唱歌和玩游戏"
        )
        
        print("\n渲染后的提示词:")
        print(rendered)
    except ValueError as e:
        print(f"渲染失败: {e}")
    
    # 清理示例文件
    os.remove(example_file)
    print(f"\n已清理示例文件: {example_file}")


if __name__ == "__main__":
    example_usage()
