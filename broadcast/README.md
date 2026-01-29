该文件夹包含直播监控弹幕相关的代码
代码源自blivedm项目
地址：https://github.com/xfgryujk/blivedm

数据发送/返回格式：json
心跳
message_data = {
            "type": "heartbeat",
            "room_id": client.room_id,
            "popularity": message.popularity
        }

弹幕
message_data = {
            "type": "danmaku",
            "room_id": client.room_id,
            "user": {
                "uid": message.uid,
                "uname": message.uname,
                "admin": message.admin,
                "vip": message.vip,
                "svip": message.svip,
                "user_level": message.user_level
            },
            "content": message.msg,
            "timestamp": message.timestamp,
            "color": message.color,
            "font_size": message.font_size,
            "mode": message.mode,
            "medal": {
                "level": message.medal_level,
                "name": message.medal_name,
                "room_id": message.medal_room_id,
                "anchor_name": message.runame
            }
        }
礼物
message_data = {
            "type": "gift",
            "room_id": client.room_id,
            "user": {
                "uid": message.uid,
                "uname": message.uname,
                "guard_level": message.guard_level
            },
            "gift": {
                "name": message.gift_name,
                "id": message.gift_id,
                "type": message.gift_type,
                "num": message.num,
                "price": message.price,
                "total_coin": message.total_coin,
                "coin_type": message.coin_type
            },
            "timestamp": message.timestamp,
            "medal": {
                "level": message.medal_level,
                "name": message.medal_name,
                "room_id": message.medal_room_id,
                "anchor_id": message.medal_ruid
            }
        }
上舰
message_data = {
                "type": "user_toast_v2",
                "room_id": client.room_id,
                "user": {
                    "uid": message.uid,
                    "username": message.username
                },
                "guard": {
                    "level": message.guard_level,
                    "num": message.num,
                    "price": message.price,
                    "unit": message.unit,
                    "gift_id": message.gift_id
                },
                "toast_msg": message.toast_msg
                }
醒目留言
message_data = {
            "type": "super_chat",
            "room_id": client.room_id,
            "user": {
                "uid": message.uid,
                "uname": message.uname,
                "guard_level": message.guard_level,
                "user_level": message.user_level
            },
            "message": message.message,
            "price": message.price,
            "start_time": message.start_time,
            "end_time": message.end_time,
            "time": message.time,
            "background": {
                "color": message.background_color,
                "bottom_color": message.background_bottom_color,
                "price_color": message.background_price_color,
                "image": message.background_image,
                "icon": message.background_icon
            },
            "gift": {
                "id": message.gift_id,
                "name": message.gift_name
            },
            "medal": {
                "level": message.medal_level,
                "name": message.medal_name,
                "room_id": message.medal_room_id,
                "anchor_id": message.medal_ruid
            }
        }
