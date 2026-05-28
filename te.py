# qq_ai_bot.py
import botpy
import os
from botpy.message import GroupMessage
from openai import OpenAI
API_KEY=os.getenv("API_KEY")
MODEL=os.getenv("MODEL")
API_URL=os.getenv("API_URL")
QQ_APPID=os.getenv("QQ_APPID")
QQ_APPKEY=os.getenv("QQ_APPKEY")
# AI 客户端 - 兼容 OpenAI 协议的都能用
cilent = OpenAI(
    api_key=API_KEY,
    base_url=API_URL  # 我用的聚合接口，Claude/GPT/Gemini 随便切
)

with open('catgirl_qq.txt','r',encoding="utf-8") as f:
    content_role=f.read()

memory=[{
    "role":"system",
    "content":content_role
}]

class MyBot(botpy.Client):
    async def on_group_at_message_create(self, message: GroupMessage):
        """群里被 @ 时触发"""
        user_msg = message.content.strip()
        if not user_msg:
            return
        # 调大模型
        print(user_msg)
        memory.append({"role":"user","content":user_msg})
        resp = cilent.chat.completions.create(
            model=MODEL,  # 换成任意模型
            messages=memory,
            max_tokens=6000
        )
        
        answer = resp.choices[0].message.content
        answer=answer.replace(".",". ")
        print(answer)
        memory.append({"role":"assistant","content":answer})
        # 回复消息
        await message.reply(content=answer)

intents = botpy.Intents(public_messages=True)
client = MyBot(intents=intents)
client.run(appid=QQ_APPID, secret=QQ_APPKEY)
