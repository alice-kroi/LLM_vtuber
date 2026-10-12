#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""face_dataset：VTS 参数 + 录音 + 文字 三模态同步采集与对齐。

- sync_recorder.py  采集（VTS 参数 CSV + 录音 WAV + 对齐元数据 JSON）
- asr.py            录音转文字（后端可插拔）
- align.py          三模态时序对齐（ASR 分段 ↔ VTS 参数帧）
"""
