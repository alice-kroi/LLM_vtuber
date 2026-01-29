#!/usr/bin/env python3
"""
Live2D主程序测试客户端
用于模拟发送消息到live2d_main.py的服务器端口
"""

import socket
import json
import time

def send_message(host, port, message):
    """发送消息到服务器"""
    try:
        # 创建套接字
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        # 连接到服务器
        client_socket.connect((host, port))
        
        # 发送消息
        json_message = json.dumps(message)
        client_socket.sendall(json_message.encode("utf-8"))
        
        # 接收响应
        response = client_socket.recv(1024).decode("utf-8")
        
        # 关闭连接
        client_socket.close()
        
        return json.loads(response)
        
    except Exception as e:
        print(f"发送消息时发生错误: {str(e)}")
        return None

def test_expression():
    """测试设置表情"""
    message = {
        "action_type": "expression",
        "emotion": "happy",
        "duration": 2.0,
        "immediate": True,
        "extra_params": {
            "blend_weight": 1.0
        }
    }
    
    print("测试设置表情...")
    response = send_message("localhost", 8080, message)
    print(f"响应: {response}")
    print()

def test_motion():
    """测试播放动作"""
    message = {
        "action_type": "motion",
        "emotion": "neutral",
        "duration": 3.0,
        "immediate": False,
        "extra_params": {
            "motion_name": "wave",
            "loop": False
        }
    }
    
    print("测试播放动作...")
    response = send_message("localhost", 8080, message)
    print(f"响应: {response}")
    print()

def test_pose():
    """测试设置姿势"""
    message = {
        "action_type": "pose",
        "emotion": "surprised",
        "duration": 1.5,
        "immediate": True,
        "extra_params": {
            "pose_name": "hands_up",
            "strength": 0.8
        }
    }
    
    print("测试设置姿势...")
    response = send_message("localhost", 8080, message)
    print(f"响应: {response}")
    print()

def test_parameter():
    """测试设置参数"""
    message = {
        "action_type": "parameter",
        "emotion": "angry",
        "duration": 1.0,
        "immediate": True,
        "extra_params": {
            "param_name": "ParamAngry",
            "value": 0.7
        }
    }
    
    print("测试设置参数...")
    response = send_message("localhost", 8080, message)
    print(f"响应: {response}")
    print()

def test_unknown_action():
    """测试未知动作类型"""
    message = {
        "action_type": "unknown",
        "emotion": "neutral",
        "duration": 1.0,
        "immediate": False,
        "extra_params": {}
    }
    
    print("测试未知动作类型...")
    response = send_message("localhost", 8080, message)
    print(f"响应: {response}")
    print()

def main():
    """主函数"""
    print("Live2D主程序测试客户端")
    print("=" * 50)
    print()
    
    # 等待服务器启动
    print("等待服务器启动...")
    time.sleep(2)
    
    # 运行所有测试
    test_expression()
    test_motion()
    test_pose()
    test_parameter()
    test_unknown_action()
    
    print("所有测试完成!")

if __name__ == "__main__":
    main()