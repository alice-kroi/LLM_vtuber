#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单验证JSON文件语法
"""

import json
import os

def validate_json_file(file_path):
    """
    验证JSON文件语法
    """
    print(f"验证文件: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # 逐行读取并尝试解析，直到遇到注释
            lines = []
            for line in f:
                line = line.strip()
                if line.startswith('//'):
                    break
                if line:
                    lines.append(line)
            
            # 尝试解析收集的行
            content = ''.join(lines)
            if content:
                data = json.loads(content)
                print(f"✓ {file_path} 语法正确")
                return True
            else:
                print(f"✗ {file_path} 内容为空")
                return False
    except json.JSONDecodeError as e:
        print(f"✗ {file_path} 语法错误: {e}")
        return False
    except Exception as e:
        print(f"✗ {file_path} 错误: {e}")
        return False

def main():
    """
    主函数
    """
    print("=== 验证JSON文件语法 ===")
    
    # 获取项目根目录
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # 验证 message_state_schema.json
    message_state_schema = os.path.join(project_root, "LLM", "message_state_schema.json")
    validate_json_file(message_state_schema)
    
    # 验证 langgraph_state_schema.json
    langgraph_state_schema = os.path.join(project_root, "langgraph_state_schema.json")
    validate_json_file(langgraph_state_schema)
    
    print("=== 验证完成 ===")

if __name__ == "__main__":
    main()
