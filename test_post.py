import requests
import json
from openai import OpenAI
{
#通过http协议直接获得回答
# url="https://api2.aigcbest.top/v1/chat/completions"
# api_key="sk-w2CdZvyZH1GiPRNEc1ZwMw7g16Gg28Lat6I5dHJLXJ3WuiXW"
# headers={
#     "Authorization":f"Bearer {api_key}",
#     "Content-Type":"application/json"
# }
# data = {
#     "model": "doubao-1-5-lite-32k-250115", 
#     "messages": [
#         {"role": "user", "content":input()}
#     ]
# }
# response=requests.post(url,headers=headers,json=data)
# print(response.json()['choices'][0]['message']['content'])``
# # print(response.json())
}
#下面统一使用gpt-3.5-turbo
cilent=OpenAI(api_key="sk-nhj5mTAnsnAKDHTIbpHwMDZ39nO7firbpN0YahiubST4yadR",base_url="https://api2.aigcbest.top/v1")
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
    # print(answer)
    memory.append({"role":"assistant","content":answer})

