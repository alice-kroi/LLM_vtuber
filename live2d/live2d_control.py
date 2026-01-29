#!/usr/bin/env python3
"""
Live2D控制模块
提供动作控制函数，参数固定为：动作类型，情感，持续时间，是否立即执行，额外参数
"""

def execute_action(action_type, emotion="neutral", duration=1.0, immediate=False, extra_params=None):
    """
    执行Live2D动作
    
    参数:
        action_type: str - 动作类型
        emotion: str - 情感状态 (默认: "neutral")
        duration: float - 动作持续时间 (秒，默认: 1.0)
        immediate: bool - 是否立即执行 (默认: False)
        extra_params: dict - 额外参数 (默认: None)
    
    返回:
        dict - 执行结果
    """
    if extra_params is None:
        extra_params = {}
    
    # 根据动作类型执行相应的动作
    try:
        if action_type == "expression":
            return set_expression(emotion, duration, immediate, extra_params)
        elif action_type == "motion":
            return play_motion(extra_params.get("motion_name", ""), emotion, duration, immediate, extra_params)
        elif action_type == "pose":
            return set_pose(extra_params.get("pose_name", ""), emotion, duration, immediate, extra_params)
        elif action_type == "parameter":
            return set_parameter(extra_params.get("param_name", ""), extra_params.get("value", 0.0), duration, immediate, extra_params)
        else:
            return {
                "success": False,
                "message": f"未知的动作类型: {action_type}"
            }
    
    except Exception as e:
        return {
            "success": False,
            "message": f"执行动作时发生错误: {str(e)}"
        }

def set_expression(emotion, duration=1.0, immediate=False, extra_params=None):
    """
    设置表情
    """
    if extra_params is None:
        extra_params = {}
    
    # 这里是设置表情的具体实现
    print(f"设置表情: {emotion}, 持续时间: {duration}秒, 立即执行: {immediate}, 额外参数: {extra_params}")
    
    # 模拟执行过程
    # 在实际应用中，这里会调用Live2D SDK或发送命令到VTuber Studio
    
    return {
        "success": True,
        "message": f"表情 '{emotion}' 设置成功",
        "action": "expression",
        "emotion": emotion,
        "duration": duration,
        "immediate": immediate,
        "extra_params": extra_params
    }

def play_motion(motion_name, emotion="neutral", duration=1.0, immediate=False, extra_params=None):
    """
    播放动作
    """
    if extra_params is None:
        extra_params = {}
    
    if not motion_name:
        return {
            "success": False,
            "message": "缺少动作名称参数"
        }
    
    # 这里是播放动作的具体实现
    print(f"播放动作: {motion_name}, 情感: {emotion}, 持续时间: {duration}秒, 立即执行: {immediate}, 额外参数: {extra_params}")
    
    # 模拟执行过程
    # 在实际应用中，这里会调用Live2D SDK或发送命令到VTuber Studio
    
    return {
        "success": True,
        "message": f"动作 '{motion_name}' 播放成功",
        "action": "motion",
        "motion_name": motion_name,
        "emotion": emotion,
        "duration": duration,
        "immediate": immediate,
        "extra_params": extra_params
    }

def set_pose(pose_name, emotion="neutral", duration=1.0, immediate=False, extra_params=None):
    """
    设置姿势
    """
    if extra_params is None:
        extra_params = {}
    
    if not pose_name:
        return {
            "success": False,
            "message": "缺少姿势名称参数"
        }
    
    # 这里是设置姿势的具体实现
    print(f"设置姿势: {pose_name}, 情感: {emotion}, 持续时间: {duration}秒, 立即执行: {immediate}, 额外参数: {extra_params}")
    
    # 模拟执行过程
    # 在实际应用中，这里会调用Live2D SDK或发送命令到VTuber Studio
    
    return {
        "success": True,
        "message": f"姿势 '{pose_name}' 设置成功",
        "action": "pose",
        "pose_name": pose_name,
        "emotion": emotion,
        "duration": duration,
        "immediate": immediate,
        "extra_params": extra_params
    }

def set_parameter(param_name, value, duration=1.0, immediate=False, extra_params=None):
    """
    设置Live2D参数
    """
    if extra_params is None:
        extra_params = {}
    
    if not param_name:
        return {
            "success": False,
            "message": "缺少参数名称"
        }
    
    # 这里是设置参数的具体实现
    print(f"设置参数: {param_name} = {value}, 持续时间: {duration}秒, 立即执行: {immediate}, 额外参数: {extra_params}")
    
    # 模拟执行过程
    # 在实际应用中，这里会调用Live2D SDK或发送命令到VTuber Studio
    
    return {
        "success": True,
        "message": f"参数 '{param_name}' 设置为 {value} 成功",
        "action": "parameter",
        "param_name": param_name,
        "value": value,
        "duration": duration,
        "immediate": immediate,
        "extra_params": extra_params
    }

def list_available_actions():
    """
    获取可用的动作类型列表
    
    返回:
        list - 可用的动作类型
    """
    return [
        "expression",  # 设置表情
        "motion",      # 播放动作
        "pose",        # 设置姿势
        "parameter"    # 设置参数
    ]

def list_available_emotions():
    """
    获取可用的情感状态列表
    
    返回:
        list - 可用的情感状态
    """
    return [
        "neutral",  # 中性
        "happy",    # 开心
        "sad",      # 悲伤
        "angry",    # 生气
        "surprised",# 惊讶
        "fearful",  # 恐惧
        "disgusted" # 厌恶
    ]