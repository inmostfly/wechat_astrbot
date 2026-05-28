from openai import OpenAI
from wxauto4 import WeChat
import os
import time
import sys

API_KEY = os.getenv("API_KEY")
API_URL = os.getenv("API_URL", "https://api2.aigcbest.top/v1")
if not API_KEY:
    print("❌ 错误：缺少 API_KEY 环境变量")
    sys.exit(1)

print("✅ API_KEY 已加载")
client = OpenAI(api_key=API_KEY, base_url=API_URL)

with open('catgirl_qq.txt', 'r', encoding='utf-8') as f:
    content = f.read()

memory = [{"role": "system", "content": content}]

wx = WeChat()
wx.ChatWith(who="Inmost", exact=False)

# 用来记录最后一次处理过的【好友消息】，这是防止死循环的核心
last_processed_friend_msg = ""

print("🚀 开始监听消息...")

while True:
    try:
        msg_list = wx.GetAllMessage()
        if not msg_list:
            time.sleep(2)
            continue
            
        # 倒序遍历，精准找到屏幕上最新的一条【好友】发来的消息
        last_friend_msg = None
        for msg in reversed(msg_list):
            if msg.type == "friend":
                last_friend_msg = msg
                break
                
        if last_friend_msg:
            user_content = last_friend_msg.content
            
            # 只有当这条好友消息的内容与上一次处理的不同时，才触发回复
            if user_content != last_processed_friend_msg:
                
                # 遇到拍一拍或系统撤回，只记录不回复
                if "拍了拍" in user_content or not user_content.strip():
                    last_processed_friend_msg = user_content
                    continue
                    
                # 【关键点】在调用大模型之前，立刻更新记录，锁死状态！
                last_processed_friend_msg = user_content
                
                user_q = "你说:" + user_content
                
                if user_content.strip() == "退出":
                    print("拜拜!")
                    sys.exit()

                if user_content.strip() == "清空记忆":
                    memory = [{"role": "system", "content": content}]
                    print("已清空对话记忆，让我们重新认识吧！")
                    continue

                memory.append({"role": "user", "content": user_q})
                print(f"📤 发送给 AI: {user_q[:30]}...")
                
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=memory,
                )
                answer = response.choices[0].message.content
                print(f"📥 AI 回复: {answer[:50]}...")
                
                wx.SendMsg(answer)
                print("✅ 消息已发送")
                memory.append({"role": "assistant", "content": answer})
        
        # 必须留出缓冲时间给微信 UI 渲染新消息
        time.sleep(2)
        
    except Exception as e:
        print(f"⚠️ 错误: {e}")
        time.sleep(3)