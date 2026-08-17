import os
import requests
import json
from openai import OpenAI

API_KEY = os.getenv("API_KEY")
API_URL = os.getenv("API_URL", "https://api2.aigcbest.top/v1")
if not API_KEY:
    raise RuntimeError("Missing API_KEY environment variable")

# 通过 http 协议直接获得回答
# url="https://api2.aigcbest.top/v1/chat/completions"
# headers={
#     "Authorization":f"Bearer {API_KEY}",
#     "Content-Type":"application/json"
# }
# data = {
#     "model": "doubao-1-5-lite-32k-250115", 
#     "messages": [
#         {"role": "user", "content":input()}
#     ]
# }
# response=requests.post(url,headers=headers,json=data)
# print(response.json()['choices'][0]['message']['content'])
# print(response.json())

# 下面统一使用 gpt-3.5-turbo
cilent = OpenAI(api_key=API_KEY, base_url=API_URL)
with open('catgirl_blog.txt','r',encoding='utf-8') as f:
    content=f.read()
memory=[{
    "role":"system",
    "content":content
}]
while True:
    user_q=input("你说：")
    if(user_q.strip()=="退出"):
        print("拜拜!")
        break

    if user_q.strip()=="清空记忆":
        memory=[{  "role":"system","content":"你是一个问答助手"}]
        print("已清空对话记忆，让我们重新认识吧！")
        continue

    memory.append({"role":"user","content":user_q})
    response=cilent.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=memory,
        stream=True
    )
    answer=" "
    for chunk in response:
        if not chunk.choices:
            continue
        now=chunk.choices[0].delta.content
        if now:
            print(now,end="",flush=True)
            answer+=now
        
    print("")
    print(answer)
    memory.append({"role":"assistant","content":answer})

