#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web远程桌面控制系统 - 改进的服务端
优化延迟和文字输入处理
"""

import asyncio
import websockets
import json
import base64
import io
import threading
import time
from flask import Flask, render_template_string, request, jsonify
from flask_cors import CORS


class RemoteDesktopServer:
    def __init__(self):
        self.clients = set()
        self.screen_providers = set()
        self.screen_viewers = set()
        self.control_clients = set()
        self.running = True
        self.current_screen_data = None
        self.screen_info = {
            'width': 0,
            'height': 0,
            'original_width': 0,
            'original_height': 0
        }

        # 性能统计
        self.stats = {
            'messages_received': 0,
            'messages_sent': 0,
            'start_time': time.time()
        }

    async def register_client(self, websocket, client_type='viewer'):
        """注册新客户端"""
        self.clients.add(websocket)

        if client_type == 'provider':
            self.screen_providers.add(websocket)
            print(f"[+] 屏幕提供者已连接，当前提供者数: {len(self.screen_providers)}")
        elif client_type == 'controller':
            self.control_clients.add(websocket)
            print(f"[+] 控制客户端已连接，当前控制者数: {len(self.control_clients)}")
        else:
            self.screen_viewers.add(websocket)
            print(f"[+] 观看客户端已连接，当前观看者数: {len(self.screen_viewers)}")

        print(f"总连接数: {len(self.clients)}")

        # 发送当前屏幕数据给新的观看者
        if client_type == 'viewer' and self.current_screen_data:
            try:
                await websocket.send(json.dumps(self.current_screen_data))
                self.stats['messages_sent'] += 1
            except Exception as e:
                print(f"发送屏幕数据给新客户端失败: {e}")

    async def unregister_client(self, websocket):
        """注销客户端"""
        self.clients.discard(websocket)
        was_provider = websocket in self.screen_providers
        was_viewer = websocket in self.screen_viewers
        was_controller = websocket in self.control_clients

        self.screen_providers.discard(websocket)
        self.screen_viewers.discard(websocket)
        self.control_clients.discard(websocket)

        client_type = "提供者" if was_provider else (
            "观看者" if was_viewer else ("控制者" if was_controller else "未知"))
        print(f"[-] {client_type}客户端已断开，当前连接数: {len(self.clients)}")

    async def handle_screen_data(self, data):
        """处理屏幕数据 - 优化版本"""
        try:
            # 更新当前屏幕数据
            self.current_screen_data = {
                'type': 'screenshot',
                'data': data.get('data'),
                'width': data.get('width', 0),
                'height': data.get('height', 0),
                'original_width': data.get('original_width', 0),
                'original_height': data.get('original_height', 0),
                'timestamp': time.time()
            }

            # 更新屏幕信息
            self.screen_info.update({
                'width': data.get('width', 0),
                'height': data.get('height', 0),
                'original_width': data.get('original_width', 0),
                'original_height': data.get('original_height', 0)
            })

            # 并发转发给所有观看客户端
            if self.screen_viewers:
                message = json.dumps(self.current_screen_data)
                await self._broadcast_to_viewers(message)

        except Exception as e:
            print(f"处理屏幕数据错误: {e}")

    async def _broadcast_to_viewers(self, message):
        """并发广播消息给观看者"""
        if not self.screen_viewers:
            return

        # 创建发送任务
        send_tasks = []
        for viewer in list(self.screen_viewers):  # 复制列表避免修改冲突
            send_tasks.append(self._send_to_client(viewer, message))

        # 并发执行所有发送任务
        results = await asyncio.gather(*send_tasks, return_exceptions=True)

        # 处理发送结果
        disconnected = set()
        for viewer, result in zip(list(self.screen_viewers), results):
            if isinstance(result, Exception):
                print(f"发送屏幕数据错误: {result}")
                disconnected.add(viewer)
            else:
                self.stats['messages_sent'] += 1

        # 移除断开的连接
        for client in disconnected:
            await self.unregister_client(client)

    async def _send_to_client(self, client, message):
        """发送消息给单个客户端"""
        try:
            if client.state == websockets.protocol.State.OPEN:
                await client.send(message)
                return True
            else:
                raise websockets.exceptions.ConnectionClosed(None, None)
        except Exception as e:
            raise e

    async def handle_control_event(self, websocket, data):
        """处理控制事件 - 优化版本"""
        try:
            if not self.screen_providers:
                print("⚠️  没有可用的屏幕提供者来处理控制事件")
                return

            # 记录控制事件用于调试
            event_type = data.get('type')
            if event_type == 'keyboard':
                keyboard_event = data.get('event_type')
                if keyboard_event == 'type':
                    text = data.get('text', '')
                    print(f"🔤 转发文字输入: '{text}'")
                elif keyboard_event == 'hotkey':
                    keys = data.get('keys', [])
                    print(f"⌨️  转发快捷键: {'+'.join(keys)}")
                else:
                    key = data.get('key', '')
                    print(f"🔑 转发按键: {key}")
            elif event_type == 'mouse':
                mouse_event = data.get('event_type')
                x, y = data.get('x', 0), data.get('y', 0)
                print(f"🖱️  转发鼠标事件: {mouse_event} at ({x}, {y})")

            # 并发转发给所有屏幕提供者
            message = json.dumps(data)
            await self._broadcast_to_providers(message)

        except Exception as e:
            print(f"处理控制事件错误: {e}")

    async def _broadcast_to_providers(self, message):
        """并发广播消息给提供者"""
        if not self.screen_providers:
            return

        send_tasks = []
        for provider in list(self.screen_providers):
            send_tasks.append(self._send_to_client(provider, message))

        results = await asyncio.gather(*send_tasks, return_exceptions=True)

        disconnected = set()
        for provider, result in zip(list(self.screen_providers), results):
            if isinstance(result, Exception):
                print(f"转发控制事件错误: {result}")
                disconnected.add(provider)
            else:
                self.stats['messages_sent'] += 1

        for client in disconnected:
            await self.unregister_client(client)

    async def handle_client_message(self, websocket, message):
        """处理客户端消息 - 优化版本"""
        try:
            data = json.loads(message)
            msg_type = data.get('type')
            self.stats['messages_received'] += 1

            if msg_type == 'register':
                client_type = data.get('client_type', 'viewer')
                await self.register_client(websocket, client_type)

                await websocket.send(json.dumps({
                    'type': 'register_ack',
                    'client_type': client_type,
                    'screen_info': self.screen_info
                }))
                self.stats['messages_sent'] += 1

            elif msg_type == 'screen_data':
                await self.handle_screen_data(data)

            elif msg_type in ['mouse', 'keyboard']:
                await self.handle_control_event(websocket, data)

            elif msg_type == 'get_screenshot':
                if self.current_screen_data:
                    await websocket.send(json.dumps(self.current_screen_data))
                    self.stats['messages_sent'] += 1
                else:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': '当前没有可用的屏幕数据'
                    }))
                    self.stats['messages_sent'] += 1

            elif msg_type == 'ping':
                await websocket.send(json.dumps({'type': 'pong'}))
                self.stats['messages_sent'] += 1

        except json.JSONDecodeError:
            print(f"❌ 无效的JSON消息: {message}")
        except Exception as e:
            print(f"❌ 处理客户端消息错误: {e}")

    async def handle_client(self, websocket):
        """处理客户端连接 - 优化版本"""
        client_address = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        print(f"🔗 新客户端连接: {client_address}")

        await self.register_client(websocket)
        try:
            async for message in websocket:
                await self.handle_client_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            print(f"🔌 客户端连接正常关闭: {client_address}")
        except Exception as e:
            print(f"❌ 客户端连接错误 {client_address}: {e}")
        finally:
            await self.unregister_client(websocket)

    def start_websocket_server(self):
        """启动WebSocket服务器"""
        return websockets.serve(
            self.handle_client,
            "0.0.0.0",
            8765,
            # 优化WebSocket设置
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
            max_size=10 * 1024 * 1024,  # 10MB max message size
            compression=None  # 禁用压缩以减少延迟
        )

    def get_server_status(self):
        """获取服务器状态"""
        runtime = time.time() - self.stats['start_time']
        return {
            'total_clients': len(self.clients),
            'screen_providers': len(self.screen_providers),
            'screen_viewers': len(self.screen_viewers),
            'control_clients': len(self.control_clients),
            'has_screen_data': self.current_screen_data is not None,
            'screen_info': self.screen_info,
            'stats': {
                'runtime': runtime,
                'messages_received': self.stats['messages_received'],
                'messages_sent': self.stats['messages_sent'],
                'avg_msg_per_sec': (self.stats['messages_received'] + self.stats['messages_sent']) / max(1, runtime)
            }
        }

    def print_statistics(self):
        """打印服务器统计信息"""
        status = self.get_server_status()
        stats = status['stats']
        print(f"\n📊 服务器统计 (运行时间: {stats['runtime']:.1f}秒)")
        print(
            f"   连接: 总计={status['total_clients']} | 提供者={status['screen_providers']} | 观看者={status['screen_viewers']} | 控制者={status['control_clients']}")
        print(
            f"   消息: 接收={stats['messages_received']} | 发送={stats['messages_sent']} | 平均速率={stats['avg_msg_per_sec']:.1f}/秒")
        print(
            f"   屏幕: {status['screen_info']['width']}x{status['screen_info']['height']} | 有数据: {'是' if status['has_screen_data'] else '否'}")


# 改进的HTML模板
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Web远程桌面控制系统 (改进版)</title>
    <style>
        body {
            margin: 0;
            padding: 20px;
            font-family: 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.2);
            overflow: hidden;
            backdrop-filter: blur(10px);
        }
        .header {
            background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
            color: white;
            padding: 20px 25px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
            font-weight: 600;
        }
        .status {
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .status::before {
            content: "●";
            font-size: 12px;
        }
        .status.connected { background: #27ae60; }
        .status.disconnected { background: #e74c3c; }
        .status.connecting { background: #f39c12; }

        .client-type-selector {
            padding: 20px 25px;
            background: #ecf0f1;
            border-bottom: 2px solid #bdc3c7;
        }
        .client-type-selector h3 {
            margin: 0 0 15px 0;
            color: #2c3e50;
            font-size: 16px;
        }
        .radio-group {
            display: flex;
            gap: 20px;
            margin-bottom: 15px;
        }
        .radio-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 15px;
            background: white;
            border-radius: 8px;
            border: 2px solid transparent;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        .radio-item:hover {
            border-color: #3498db;
            transform: translateY(-2px);
        }
        .radio-item input:checked + label {
            color: #3498db;
            font-weight: 600;
        }

        .controls {
            padding: 20px 25px;
            background: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }

        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .btn:hover { 
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        .btn-primary { background: #3498db; color: white; }
        .btn-secondary { background: #95a5a6; color: white; }
        .btn-success { background: #27ae60; color: white; }
        .btn-warning { background: #f39c12; color: white; }

        .screen-container {
            position: relative;
            overflow: auto;
            max-height: 70vh;
            text-align: center;
            background: #2c3e50;
            min-height: 400px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        #desktop-screen {
            max-width: 100%;
            height: auto;
            cursor: crosshair;
            display: block;
            margin: 0 auto;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }
        .no-screen {
            color: #ecf0f1;
            font-size: 18px;
            font-weight: 300;
        }

        .input-panel {
            padding: 25px;
            background: #f8f9fa;
        }
        .input-group {
            margin-bottom: 20px;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .input-group h4 {
            margin: 0 0 15px 0;
            color: #2c3e50;
            font-size: 16px;
        }
        .input-group input[type="text"] {
            width: 100%;
            max-width: 400px;
            padding: 12px;
            border: 2px solid #bdc3c7;
            border-radius: 6px;
            font-size: 14px;
            transition: border-color 0.3s ease;
        }
        .input-group input[type="text"]:focus {
            outline: none;
            border-color: #3498db;
        }

        .hotkey-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
            margin-top: 15px;
        }

        .server-info {
            padding: 15px 25px;
            background: linear-gradient(135deg, #f39c12 0%, #e67e22 100%);
            color: white;
            font-size: 13px;
            font-weight: 500;
        }

        .info-card {
            margin-top: 20px;
            padding: 20px;
            background: linear-gradient(135deg, #74b9ff 0%, #0984e3 100%);
            color: white;
            border-radius: 8px;
            font-size: 14px;
            line-height: 1.6;
        }
        .info-card h4 {
            margin: 0 0 15px 0;
            font-size: 16px;
        }
        .info-card ul {
            margin: 10px 0;
            padding-left: 20px;
        }

        @media (max-width: 768px) {
            .container { margin: 10px; }
            .radio-group { flex-direction: column; }
            .controls { flex-direction: column; }
            .hotkey-grid { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🖥️ Web远程桌面控制系统 (改进版)</h1>
            <div class="status" id="connection-status">未连接</div>
        </div>

        <div class="client-type-selector">
            <h3>选择客户端类型:</h3>
            <div class="radio-group">
                <div class="radio-item">
                    <input type="radio" name="client-type" value="viewer" id="viewer" checked>
                    <label for="viewer">👀 观看者 (可查看和控制)</label>
                </div>
                <div class="radio-item">
                    <input type="radio" name="client-type" value="provider" id="provider">
                    <label for="provider">📺 屏幕提供者</label>
                </div>
                <div class="radio-item">
                    <input type="radio" name="client-type" value="controller" id="controller">
                    <label for="controller">🎮 控制者 (仅控制)</label>
                </div>
            </div>
            <button class="btn btn-primary" onclick="reconnectWithType()">🔄 重新连接</button>
        </div>

        <div class="server-info" id="server-info">
            📊 服务器状态加载中...
        </div>

        <div class="controls" id="controls-panel" style="display:none;">
            <button class="btn btn-primary" onclick="takeScreenshot()">📸 请求截图</button>
            <button class="btn btn-secondary" onclick="reconnect()">🔄 重新连接</button>
            <button class="btn btn-warning" onclick="clearScreen()">🗑️ 清除显示</button>
        </div>

        <div class="screen-container">
            <img id="desktop-screen" alt="桌面截图" style="display:none;" />
            <div id="no-screen" class="no-screen">⏳ 等待屏幕数据...</div>
        </div>

        <div class="input-panel" id="input-panel" style="display:none;">
            <div class="input-group">
                <h4>💬 文本输入</h4>
                <input type="text" id="text-input" placeholder="输入要发送到远程桌面的文字..." maxlength="1000">
                <button class="btn btn-success" onclick="sendText()" style="margin-left: 10px;">📝 发送文字</button>
            </div>

            <div class="input-group">
                <h4>⌨️ 快捷键操作</h4>
                <div class="hotkey-grid">
                    <button class="btn btn-secondary" onclick="sendHotkey(['ctrl', 'c'])">Ctrl+C</button>
                    <button class="btn btn-secondary" onclick="sendHotkey(['ctrl', 'v'])">Ctrl+V</button>
                    <button class="btn btn-secondary" onclick="sendHotkey(['ctrl', 'a'])">Ctrl+A</button>
                    <button class="btn btn-secondary" onclick="sendHotkey(['ctrl', 'z'])">Ctrl+Z</button>
                    <button class="btn btn-secondary" onclick="sendHotkey(['alt', 'tab'])">Alt+Tab</button>
                    <button class="btn btn-secondary" onclick="sendKey('enter')">Enter</button>
                    <button class="btn btn-secondary" onclick="sendKey('esc')">Esc</button>
                    <button class="btn btn-secondary" onclick="sendKey('delete')">Delete</button>
                </div>
            </div>

            <div class="info-card">
                <h4>📋 使用说明</h4>
                <ul>
                    <li><strong>观看者:</strong> 可以查看屏幕并进行所有控制操作</li>
                    <li><strong>屏幕提供者:</strong> 发送屏幕数据到服务器供其他人查看</li>
                    <li><strong>控制者:</strong> 专门用于发送控制指令而不显示屏幕</li>
                    <li>点击屏幕图像进行鼠标操作，支持左键、右键和滚轮</li>
                    <li>使用文本输入框发送文字，支持中文输入</li>
                    <li>使用快捷键按钮发送组合键操作</li>
                </ul>
            </div>
        </div>
    </div>

    <script>
        let ws = null;
        let isConnected = false;
        let clientType = 'viewer';
        let lastClick = 0;
        let reconnectAttempts = 0;

        // 连接WebSocket
        function connect() {
            clientType = document.querySelector('input[name="client-type"]:checked').value;

            // 动态构建WebSocket URL
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const host = window.location.hostname;
            const wsUrl = `${protocol}//${host}:8765`;

            ws = new WebSocket(wsUrl);
            updateStatus('connecting', '连接中...');

            ws.onopen = function(event) {
                console.log('✅ WebSocket连接已建立');
                reconnectAttempts = 0;
                sendMessage({
                    type: 'register',
                    client_type: clientType
                });
            };

            ws.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    handleServerMessage(data);
                } catch (e) {
                    console.error('❌ 解析消息错误:', e);
                }
            };

            ws.onclose = function(event) {
                isConnected = false;
                updateStatus('disconnected', `连接断开 (${event.code})`);
                console.log('🔌 WebSocket连接已关闭, 代码:', event.code);
                updateUI();

                // 自动重连逻辑
                reconnectAttempts++;
                const delay = Math.min(5000 * reconnectAttempts, 30000);
                console.log(`🔄 ${delay/1000}秒后自动重连 (第${reconnectAttempts}次)`);
                setTimeout(connect, delay);
            };

            ws.onerror = function(error) {
                console.error('❌ WebSocket错误:', error);
                updateStatus('disconnected', '连接错误');
            };
        }

        // 处理服务器消息
        function handleServerMessage(data) {
            switch(data.type) {
                case 'register_ack':
                    isConnected = true;
                    updateStatus('connected', `✅ 已连接 (${data.client_type})`);
                    updateUI();
                    if (clientType === 'viewer' || clientType === 'controller') {
                        setTimeout(takeScreenshot, 500); // 延迟请求截图
                    }
                    break;
                case 'screenshot':
                    displayScreenshot(data);
                    break;
                case 'error':
                    console.error('❌ 服务器错误:', data.message);
                    showNotification('错误: ' + data.message, 'error');
                    break;
                case 'pong':
                    console.log('💓 心跳响应收到');
                    break;
            }
        }

        // 显示截图
        function displayScreenshot(data) {
            const screenImg = document.getElementById('desktop-screen');
            const noScreen = document.getElementById('no-screen');

            try {
                screenImg.src = data.data;
                screenImg.style.display = 'block';
                noScreen.style.display = 'none';
                console.log(`🖼️ 屏幕更新: ${data.width}x${data.height}`);
            } catch (e) {
                console.error('❌ 显示截图错误:', e);
            }
        }

        // 显示通知
        function showNotification(message, type = 'info') {
            // 简单的通知实现
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                padding: 15px 20px;
                border-radius: 8px;
                color: white;
                font-weight: 500;
                z-index: 1000;
                opacity: 0;
                transition: opacity 0.3s ease;
                ${type === 'error' ? 'background: #e74c3c;' : 'background: #27ae60;'}
            `;
            notification.textContent = message;
            document.body.appendChild(notification);

            // 显示动画
            setTimeout(() => notification.style.opacity = '1', 100);

            // 3秒后自动消失
            setTimeout(() => {
                notification.style.opacity = '0';
                setTimeout(() => document.body.removeChild(notification), 300);
            }, 3000);
        }

        // 更新UI状态
        function updateUI() {
            const controlsPanel = document.getElementById('controls-panel');
            const inputPanel = document.getElementById('input-panel');

            if (isConnected && (clientType === 'viewer' || clientType === 'controller')) {
                controlsPanel.style.display = 'flex';
                inputPanel.style.display = 'block';
            } else {
                controlsPanel.style.display = 'none';
                inputPanel.style.display = 'none';
            }
        }

        // 更新连接状态
        function updateStatus(status, text) {
            const statusEl = document.getElementById('connection-status');
            statusEl.className = 'status ' + status;
            statusEl.textContent = text;
        }

        // 发送消息到服务器
        function sendMessage(message) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify(message));
                return true;
            } else {
                console.warn('⚠️ WebSocket未连接，无法发送消息');
                showNotification('连接已断开，无法发送消息', 'error');
                return false;
            }
        }

        // 获取图像坐标
        function getImageCoordinates(event) {
            const img = event.target;
            const rect = img.getBoundingClientRect();
            const scaleX = img.naturalWidth / img.width;
            const scaleY = img.naturalHeight / img.height;

            return {
                x: Math.round((event.clientX - rect.left) * scaleX),
                y: Math.round((event.clientY - rect.top) * scaleY)
            };
        }

        // 处理鼠标点击
        function handleClick(event) {
            if (clientType === 'provider' || !isConnected) return;

            event.preventDefault();
            const coords = getImageCoordinates(event);
            const now = Date.now();

            // 双击检测
            if (now - lastClick < 300) {
                sendMessage({
                    type: 'mouse',
                    event_type: 'double_click',
                    x: coords.x,
                    y: coords.y,
                    button: 'left'
                });
                console.log(`🖱️ 双击: (${coords.x}, ${coords.y})`);
            } else {
                const button = event.button === 2 ? 'right' : 'left';
                sendMessage({
                    type: 'mouse',
                    event_type: 'click',
                    x: coords.x,
                    y: coords.y,
                    button: button
                });
                console.log(`🖱️ ${button === 'right' ? '右' : '左'}键点击: (${coords.x}, ${coords.y})`);
            }

            lastClick = now;
        }

        // 处理鼠标滚轮
        function handleWheel(event) {
            if (clientType === 'provider' || !isConnected) return;

            event.preventDefault();
            const coords = getImageCoordinates(event);
            const delta = event.deltaY > 0 ? -3 : 3;

            sendMessage({
                type: 'mouse',
                event_type: 'scroll',
                x: coords.x,
                y: coords.y,
                delta: delta
            });

            console.log(`🖱️ 滚轮: ${delta > 0 ? '上' : '下'} at (${coords.x}, ${coords.y})`);
        }

        // 发送文字 - 改进版本
        function sendText() {
            if (clientType === 'provider' || !isConnected) return;

            const textInput = document.getElementById('text-input');
            const text = textInput.value.trim();

            if (text) {
                const success = sendMessage({
                    type: 'keyboard',
                    event_type: 'type',
                    text: text
                });

                if (success) {
                    console.log(`💬 发送文字: "${text}"`);
                    showNotification(`文字已发送: "${text}"`);
                    textInput.value = '';
                }
            } else {
                showNotification('请输入要发送的文字', 'error');
            }
        }

        // 发送按键
        function sendKey(key) {
            if (clientType === 'provider' || !isConnected) return;

            const success = sendMessage({
                type: 'keyboard',
                event_type: 'key',
                key: key
            });

            if (success) {
                console.log(`⌨️ 发送按键: ${key}`);
            }
        }

        // 发送组合键
        function sendHotkey(keys) {
            if (clientType === 'provider' || !isConnected) return;

            const success = sendMessage({
                type: 'keyboard',
                event_type: 'hotkey',
                keys: keys
            });

            if (success) {
                console.log(`⌨️ 发送快捷键: ${keys.join('+')}`);
                showNotification(`快捷键: ${keys.join('+')}`);
            }
        }

        // 请求截图
        function takeScreenshot() {
            if (!isConnected) return;

            sendMessage({ type: 'get_screenshot' });
            console.log('📸 请求截图');
        }

        // 清除屏幕显示
        function clearScreen() {
            const screenImg = document.getElementById('desktop-screen');
            const noScreen = document.getElementById('no-screen');

            screenImg.style.display = 'none';
            noScreen.style.display = 'block';
            noScreen.textContent = '🗑️ 屏幕已清除';
        }

        // 重新连接
        function reconnect() {
            if (ws) {
                ws.close();
            }
            reconnectAttempts = 0;
            setTimeout(connect, 100);
        }

        // 使用新类型重新连接
        function reconnectWithType() {
            reconnect();
        }

        // 更新服务器信息
        function updateServerInfo() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    const stats = data.stats || {};
                    document.getElementById('server-info').innerHTML = 
                        `📊 连接统计: 总计 ${data.total_clients} | 提供者 ${data.screen_providers} | 观看者 ${data.screen_viewers} | 控制者 ${data.control_clients} | ` +
                        `消息处理: ${stats.messages_received || 0}↓ ${stats.messages_sent || 0}↑ | ` +
                        `屏幕: ${data.has_screen_data ? '✅' : '❌'} ${data.screen_info.width}x${data.screen_info.height}`;
                })
                .catch(error => {
                    console.error('❌ 获取服务器状态失败:', error);
                    document.getElementById('server-info').innerHTML = '❌ 无法获取服务器状态';
                });
        }

        // 页面初始化
        document.addEventListener('DOMContentLoaded', function() {
            const desktopScreen = document.getElementById('desktop-screen');
            const textInput = document.getElementById('text-input');

            // 绑定图像事件
            desktopScreen.addEventListener('click', handleClick);
            desktopScreen.addEventListener('contextmenu', handleClick);
            desktopScreen.addEventListener('wheel', handleWheel);

            // 文本输入框事件
            textInput.addEventListener('keypress', function(event) {
                if (event.key === 'Enter') {
                    sendText();
                }
            });

            // 防止右键菜单
            desktopScreen.addEventListener('contextmenu', function(e) {
                e.preventDefault();
            });

            // 连接WebSocket
            connect();

            // 定期更新服务器信息
            setInterval(updateServerInfo, 2000);

            console.log('🚀 页面初始化完成');
        });

        // 心跳检测
        setInterval(function() {
            if (ws && ws.readyState === WebSocket.OPEN) {
                sendMessage({ type: 'ping' });
            }
        }, 30000);

        // 页面可见性变化处理
        document.addEventListener('visibilitychange', function() {
            if (!document.hidden && isConnected && (clientType === 'viewer' || clientType === 'controller')) {
                // 页面重新可见时请求新截图
                setTimeout(takeScreenshot, 500);
            }
        });
    </script>
</body>
</html>
'''

# Flask应用
app = Flask(__name__)
CORS(app)


@app.route('/')
def index():
    return HTML_TEMPLATE


@app.route('/api/status')
def status():
    return jsonify(server.get_server_status())


# 创建服务器实例
server = RemoteDesktopServer()


def run_flask():
    """运行Flask应用"""
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)


async def statistics_loop():
    """定期打印统计信息"""
    while server.running:
        await asyncio.sleep(60)  # 每分钟打印一次
        if len(server.clients) > 0:
            server.print_statistics()


async def main():
    """主函数"""
    print("🚀 启动Web远程桌面控制系统（改进版）...")
    print("=" * 60)
    print("📡 HTTP服务: http://localhost:5000")
    print("🔌 WebSocket服务: ws://localhost:8765")
    print("=" * 60)
    print("📋 客户端类型说明:")
    print("   👀 观看者(viewer): 可以查看屏幕并发送控制指令")
    print("   📺 屏幕提供者(provider): 发送屏幕数据到服务器")
    print("   🎮 控制者(controller): 专门发送控制指令")
    print("=" * 60)
    print("🔧 主要改进:")
    print("   • 修复文字输入无法接收的问题")
    print("   • 优化延迟性能和并发处理")
    print("   • 改进的错误处理和重连机制")
    print("   • 实时统计信息和日志")
    print("=" * 60)

    # 在新线程中启动Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 启动WebSocket服务器
    websocket_server = await server.start_websocket_server()

    # 启动统计任务
    stats_task = asyncio.create_task(statistics_loop())

    print("✅ 服务器已启动！请在浏览器中访问 http://localhost:5000")
    print("📊 按 Ctrl+C 停止服务器\n")

    try:
        await websocket_server.wait_closed()
    except KeyboardInterrupt:
        print("\n🛑 正在关闭服务器...")
        server.running = False
        stats_task.cancel()
        try:
            await stats_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    # 检查依赖
    required_packages = [
        "websockets",
        "flask",
        "flask-cors"
    ]

    print("📦 请确保已安装以下依赖包:")
    for pkg in required_packages:
        print(f"   pip install {pkg}")
    print()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 服务器已停止")
    except Exception as e:
        print(f"❌ 服务器运行错误: {e}")
        import traceback

        traceback.print_exc()