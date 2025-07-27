#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web远程桌面控制系统 - 服务端（修改版）
接受客户端传送的屏幕数据，支持实时桌面转发、鼠标控制、键盘输入
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
        self.clients = set()  # 所有连接的客户端
        self.screen_providers = set()  # 提供屏幕数据的客户端
        self.screen_viewers = set()  # 观看屏幕的客户端
        self.control_clients = set()  # 可以控制的客户端
        self.running = True
        self.current_screen_data = None  # 当前屏幕数据
        self.screen_info = {
            'width': 0,
            'height': 0,
            'original_width': 0,
            'original_height': 0
        }

    async def register_client(self, websocket, client_type='viewer'):
        """注册新客户端"""
        self.clients.add(websocket)

        if client_type == 'provider':
            self.screen_providers.add(websocket)
            print(f"屏幕提供者已连接，当前提供者数: {len(self.screen_providers)}")
        elif client_type == 'controller':
            self.control_clients.add(websocket)
            print(f"控制客户端已连接，当前控制者数: {len(self.control_clients)}")
        else:
            self.screen_viewers.add(websocket)
            print(f"观看客户端已连接，当前观看者数: {len(self.screen_viewers)}")

        print(f"总连接数: {len(self.clients)}")

        # 如果是新的观看者且有当前屏幕数据，立即发送
        if client_type == 'viewer' and self.current_screen_data:
            try:
                await websocket.send(json.dumps(self.current_screen_data))
            except:
                pass

    async def unregister_client(self, websocket):
        """注销客户端"""
        self.clients.discard(websocket)
        self.screen_providers.discard(websocket)
        self.screen_viewers.discard(websocket)
        self.control_clients.discard(websocket)
        print(f"客户端已断开，当前连接数: {len(self.clients)}")

    async def handle_screen_data(self, data):
        """处理客户端发送的屏幕数据"""
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

            # 转发给所有观看客户端
            if self.screen_viewers:
                message = json.dumps(self.current_screen_data)
                disconnected = set()

                for viewer in self.screen_viewers:
                    try:
                        await viewer.send(message)
                    except websockets.exceptions.ConnectionClosed:
                        disconnected.add(viewer)
                    except Exception as e:
                        print(f"发送屏幕数据错误: {e}")
                        disconnected.add(viewer)

                # 移除断开的连接
                for client in disconnected:
                    await self.unregister_client(client)

        except Exception as e:
            print(f"处理屏幕数据错误: {e}")

    async def handle_control_event(self, websocket, data):
        """处理控制事件，转发给屏幕提供者"""
        try:
            if not self.screen_providers:
                print("没有可用的屏幕提供者来处理控制事件")
                return

            # 转发控制事件给所有屏幕提供者
            message = json.dumps(data)
            disconnected = set()

            for provider in self.screen_providers:
                try:
                    await provider.send(message)
                except websockets.exceptions.ConnectionClosed:
                    disconnected.add(provider)
                except Exception as e:
                    print(f"转发控制事件错误: {e}")
                    disconnected.add(provider)

            # 移除断开的连接
            for client in disconnected:
                await self.unregister_client(client)

        except Exception as e:
            print(f"处理控制事件错误: {e}")

    async def handle_client_message(self, websocket, message):
        """处理客户端消息"""
        try:
            data = json.loads(message)
            msg_type = data.get('type')

            if msg_type == 'register':
                # 客户端注册类型
                client_type = data.get('client_type', 'viewer')
                await self.register_client(websocket, client_type)

                # 发送注册确认
                await websocket.send(json.dumps({
                    'type': 'register_ack',
                    'client_type': client_type,
                    'screen_info': self.screen_info
                }))

            elif msg_type == 'screen_data':
                # 处理屏幕数据（来自提供者）
                await self.handle_screen_data(data)

            elif msg_type in ['mouse', 'keyboard']:
                # 处理控制事件（来自控制者或观看者）
                await self.handle_control_event(websocket, data)

            elif msg_type == 'get_screenshot':
                # 请求当前屏幕截图
                if self.current_screen_data:
                    await websocket.send(json.dumps(self.current_screen_data))
                else:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': '当前没有可用的屏幕数据'
                    }))

            elif msg_type == 'ping':
                # 心跳检测
                await websocket.send(json.dumps({'type': 'pong'}))

        except json.JSONDecodeError:
            print(f"无效的JSON消息: {message}")
        except Exception as e:
            print(f"处理客户端消息错误: {e}")

    async def handle_client(self, websocket):
        """处理客户端连接"""
        await self.register_client(websocket)
        try:
            async for message in websocket:
                await self.handle_client_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            print(f"客户端连接错误: {e}")
        finally:
            await self.unregister_client(websocket)

    def start_websocket_server(self):
        """启动WebSocket服务器"""
        return websockets.serve(self.handle_client, "0.0.0.0", 8765)

    def get_server_status(self):
        """获取服务器状态"""
        return {
            'total_clients': len(self.clients),
            'screen_providers': len(self.screen_providers),
            'screen_viewers': len(self.screen_viewers),
            'control_clients': len(self.control_clients),
            'has_screen_data': self.current_screen_data is not None,
            'screen_info': self.screen_info
        }


# HTML模板 - 修改为支持多种客户端类型
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Web远程桌面控制系统</title>
    <style>
        body {
            margin: 0;
            padding: 20px;
            font-family: Arial, sans-serif;
            background-color: #f0f0f0;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        .header {
            background: #333;
            color: white;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .status {
            padding: 5px 10px;
            border-radius: 4px;
            font-size: 12px;
        }
        .status.connected { background: #4CAF50; }
        .status.disconnected { background: #f44336; }
        .status.connecting { background: #ff9800; }
        .client-type-selector {
            padding: 15px 20px;
            background: #e3f2fd;
            border-bottom: 1px solid #dee2e6;
        }
        .client-type-selector label {
            margin-right: 15px;
            font-weight: bold;
        }
        .client-type-selector input[type="radio"] {
            margin-right: 5px;
        }
        .controls {
            padding: 15px 20px;
            background: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
        }
        .control-group {
            display: inline-block;
            margin-right: 20px;
            margin-bottom: 10px;
        }
        .control-group label {
            display: inline-block;
            margin-right: 5px;
            font-weight: bold;
        }
        .screen-container {
            position: relative;
            overflow: auto;
            max-height: 60vh;
            text-align: center;
            background: #f5f5f5;
            min-height: 300px;
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
        }
        .no-screen {
            color: #666;
            font-size: 16px;
        }
        .input-panel {
            padding: 20px;
            background: #f8f9fa;
            border-top: 1px solid #dee2e6;
        }
        .input-group {
            margin-bottom: 15px;
        }
        .input-group label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        .input-group input[type="text"] {
            width: 300px;
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
        }
        .btn {
            padding: 8px 16px;
            margin: 0 5px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }
        .btn-primary { background: #007bff; color: white; }
        .btn-secondary { background: #6c757d; color: white; }
        .btn-success { background: #28a745; color: white; }
        .btn:hover { opacity: 0.8; }
        .info {
            margin-top: 10px;
            padding: 10px;
            background: #e9ecef;
            border-radius: 4px;
            font-size: 12px;
        }
        .server-info {
            padding: 10px 20px;
            background: #fff3cd;
            border-bottom: 1px solid #dee2e6;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Web远程桌面控制系统</h1>
            <div class="status" id="connection-status">未连接</div>
        </div>

        <div class="client-type-selector">
            <label>客户端类型:</label>
            <label><input type="radio" name="client-type" value="viewer" checked> 观看者</label>
            <label><input type="radio" name="client-type" value="provider"> 屏幕提供者</label>
            <label><input type="radio" name="client-type" value="controller"> 控制者</label>
            <button class="btn btn-primary" onclick="reconnectWithType()">重新连接</button>
        </div>

        <div class="server-info" id="server-info">
            服务器状态加载中...
        </div>

        <div class="controls" id="controls-panel" style="display:none;">
            <div class="control-group">
                <button class="btn btn-primary" onclick="takeScreenshot()">请求截图</button>
                <button class="btn btn-secondary" onclick="reconnect()">重新连接</button>
            </div>
        </div>

        <div class="screen-container">
            <img id="desktop-screen" alt="桌面截图" style="display:none;" />
            <div id="no-screen" class="no-screen">等待屏幕数据...</div>
        </div>

        <div class="input-panel" id="input-panel" style="display:none;">
            <div class="input-group">
                <label>文本输入:</label>
                <input type="text" id="text-input" placeholder="输入要发送到远程桌面的文字...">
                <button class="btn btn-success" onclick="sendText()">发送文字</button>
            </div>

            <div class="input-group">
                <label>快捷键:</label>
                <button class="btn btn-secondary" onclick="sendHotkey(['ctrl', 'c'])">Ctrl+C</button>
                <button class="btn btn-secondary" onclick="sendHotkey(['ctrl', 'v'])">Ctrl+V</button>
                <button class="btn btn-secondary" onclick="sendHotkey(['ctrl', 'a'])">Ctrl+A</button>
                <button class="btn btn-secondary" onclick="sendHotkey(['alt', 'tab'])">Alt+Tab</button>
                <button class="btn btn-secondary" onclick="sendKey('enter')">Enter</button>
                <button class="btn btn-secondary" onclick="sendKey('esc')">Esc</button>
            </div>

            <div class="info">
                <strong>使用说明:</strong><br>
                • <strong>观看者:</strong> 可以查看屏幕并进行控制操作<br>
                • <strong>屏幕提供者:</strong> 需要实现屏幕数据发送功能<br>
                • <strong>控制者:</strong> 专门用于发送控制指令<br>
                • 点击图像进行鼠标操作，使用快捷键按钮发送组合键
            </div>
        </div>
    </div>

    <script>
        let ws = null;
        let isConnected = false;
        let clientType = 'viewer';
        let lastClick = 0;

        // 连接WebSocket
        function connect() {
            clientType = document.querySelector('input[name="client-type"]:checked').value;
            ws = new WebSocket('ws://localhost:8765');
            updateStatus('connecting', '连接中...');

            ws.onopen = function(event) {
                console.log('WebSocket连接已建立');
                // 注册客户端类型
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
                    console.error('解析消息错误:', e);
                }
            };

            ws.onclose = function(event) {
                isConnected = false;
                updateStatus('disconnected', '连接断开');
                console.log('WebSocket连接已关闭');
                updateUI();
                // 5秒后自动重连
                setTimeout(connect, 5000);
            };

            ws.onerror = function(error) {
                console.error('WebSocket错误:', error);
                updateStatus('disconnected', '连接错误');
            };
        }

        // 处理服务器消息
        function handleServerMessage(data) {
            switch(data.type) {
                case 'register_ack':
                    isConnected = true;
                    updateStatus('connected', `已连接 (${data.client_type})`);
                    updateUI();
                    if (clientType === 'viewer' || clientType === 'controller') {
                        takeScreenshot();
                    }
                    break;
                case 'screenshot':
                    displayScreenshot(data);
                    break;
                case 'error':
                    console.error('服务器错误:', data.message);
                    break;
                case 'pong':
                    // 心跳响应
                    break;
            }
        }

        // 显示截图
        function displayScreenshot(data) {
            const screenImg = document.getElementById('desktop-screen');
            const noScreen = document.getElementById('no-screen');

            screenImg.src = data.data;
            screenImg.style.display = 'block';
            noScreen.style.display = 'none';
        }

        // 更新UI状态
        function updateUI() {
            const controlsPanel = document.getElementById('controls-panel');
            const inputPanel = document.getElementById('input-panel');

            if (isConnected && (clientType === 'viewer' || clientType === 'controller')) {
                controlsPanel.style.display = 'block';
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
            } else {
                console.warn('WebSocket未连接');
            }
        }

        // 获取图像上的坐标
        function getImageCoordinates(event) {
            const img = event.target;
            const rect = img.getBoundingClientRect();
            const scaleX = img.naturalWidth / img.width;
            const scaleY = img.naturalHeight / img.height;

            return {
                x: (event.clientX - rect.left) * scaleX,
                y: (event.clientY - rect.top) * scaleY
            };
        }

        // 处理鼠标点击
        function handleClick(event) {
            if (clientType === 'provider') return; // 提供者不发送控制事件

            event.preventDefault();
            const coords = getImageCoordinates(event);
            const now = Date.now();

            if (now - lastClick < 300) {
                sendMessage({
                    type: 'mouse',
                    event_type: 'double_click',
                    x: coords.x,
                    y: coords.y,
                    button: 'left'
                });
            } else {
                sendMessage({
                    type: 'mouse',
                    event_type: 'click',
                    x: coords.x,
                    y: coords.y,
                    button: event.button === 2 ? 'right' : 'left'
                });
            }

            lastClick = now;
        }

        // 处理鼠标滚轮
        function handleWheel(event) {
            if (clientType === 'provider') return;

            event.preventDefault();
            const coords = getImageCoordinates(event);

            sendMessage({
                type: 'mouse',
                event_type: 'scroll',
                x: coords.x,
                y: coords.y,
                delta: event.deltaY > 0 ? -3 : 3
            });
        }

        // 发送文字
        function sendText() {
            if (clientType === 'provider') return;

            const textInput = document.getElementById('text-input');
            const text = textInput.value;
            if (text) {
                sendMessage({
                    type: 'keyboard',
                    event_type: 'type',
                    text: text
                });
                textInput.value = '';
            }
        }

        // 发送按键
        function sendKey(key) {
            if (clientType === 'provider') return;

            sendMessage({
                type: 'keyboard',
                event_type: 'key',
                key: key
            });
        }

        // 发送组合键
        function sendHotkey(keys) {
            if (clientType === 'provider') return;

            sendMessage({
                type: 'keyboard',
                event_type: 'hotkey',
                keys: keys
            });
        }

        // 立即截图
        function takeScreenshot() {
            sendMessage({ type: 'get_screenshot' });
        }

        // 重新连接
        function reconnect() {
            if (ws) {
                ws.close();
            }
            connect();
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
                    document.getElementById('server-info').innerHTML = 
                        `连接统计: 总计 ${data.total_clients} | 提供者 ${data.screen_providers} | 观看者 ${data.screen_viewers} | 控制者 ${data.control_clients} | 有屏幕数据: ${data.has_screen_data ? '是' : '否'}`;
                })
                .catch(error => {
                    console.error('获取服务器状态失败:', error);
                });
        }

        // 初始化页面
        document.addEventListener('DOMContentLoaded', function() {
            const desktopScreen = document.getElementById('desktop-screen');
            const textInput = document.getElementById('text-input');

            // 绑定图像事件
            desktopScreen.addEventListener('click', handleClick);
            desktopScreen.addEventListener('contextmenu', handleClick);
            desktopScreen.addEventListener('wheel', handleWheel);

            // 文本输入框回车发送
            textInput.addEventListener('keypress', function(event) {
                if (event.key === 'Enter') {
                    sendText();
                }
            });

            // 连接WebSocket
            connect();

            // 定期更新服务器信息
            setInterval(updateServerInfo, 3000);
        });

        // 心跳检测
        setInterval(function() {
            if (ws && ws.readyState === WebSocket.OPEN) {
                sendMessage({ type: 'ping' });
            }
        }, 30000);
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


async def main():
    """主函数"""
    print("启动Web远程桌面控制系统（接受客户端屏幕传送版本）...")
    print("HTTP服务: http://localhost:5000")
    print("WebSocket服务: ws://localhost:8765")
    print()
    print("客户端类型说明:")
    print("- 观看者(viewer): 可以查看屏幕并发送控制指令")
    print("- 屏幕提供者(provider): 发送屏幕数据到服务器")
    print("- 控制者(controller): 专门发送控制指令")
    print()

    # 在新线程中启动Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 启动WebSocket服务器
    websocket_server = await server.start_websocket_server()

    print("服务器已启动！请在浏览器中访问 http://localhost:5000")

    try:
        await websocket_server.wait_closed()
    except KeyboardInterrupt:
        print("\n正在关闭服务器...")
        server.running = False


if __name__ == "__main__":
    # 安装依赖提示
    required_packages = [
        "websockets",
        "flask",
        "flask-cors"
    ]

    print("请确保已安装以下依赖包:")
    for pkg in required_packages:
        print(f"  pip install {pkg}")
    print()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("服务器已停止")