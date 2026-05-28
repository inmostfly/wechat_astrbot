import os
import requests
import json
import time
from wxauto import WeChat


def get_access_token():
    """
    使用 API Key, Secret Key 获取 access_token
    """
    client_id = os.getenv("BAIDU_CLIENT_ID")
    client_secret = os.getenv("BAIDU_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Missing BAIDU_CLIENT_ID or BAIDU_CLIENT_SECRET environment variable")

    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret
    }

    try:
        response = requests.get(url, params=params)
        data=response.json()
        return data.get("access_token")
    except Exception as e:
        print("获取 access_token 出错：", e)
        return None


def main(wx1, msg1, who, conversation_history):
    token = get_access_token()
    if not token:
        print("获取 access_token 失败")
        return

    # 将当前消息加入历史对话
    conversation_history.append({"role": "user", "content": msg1})

    url = f"https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/ernie-speed-128k?access_token={token}"

    payload = json.dumps({
        "messages": conversation_history
    })
    headers = {
        'Content-Type': 'application/json'
    }

    try:
        response = requests.post(url, headers=headers, data=payload)
        json_result = response.json()
        reply = json_result.get('result', '猫猫正在睡觉觉呢')
        print(f"回复「{who}」：{reply}")
        wx1.SendMsg(msg=reply + "", who=who)
        
        # 将AI的回复加入历史对话
        conversation_history.append({"role": "assistant", "content": reply})

    except Exception as e:
        print("调用文心一言失败：", e)


if __name__ == '__main__':
    wx = WeChat()
    conversation_histories = {}  # 用于存储每个联系人（sender）对应的对话历史
    last_msg = ""

    print("正在监听所有微信好友消息...")

    while True:
        try:
            msgs = wx.GetAllMessage()

            if msgs and msgs[-1].type == "friend":
                sender = msgs[-1].sender  # 获取消息来源用户名
                content = msgs[-1].content
                print(f"收到来自「{sender}」的消息：{content}")

                # 检查是否已存在该联系人的对话历史，如果没有则初始化
                if sender not in conversation_histories:
                    if sender =='李想':
                        str_prompt="你是一位温柔高贵、略带御姐风的猫娘，说话要轻柔并带有一点戏谑，要称呼我为“小家伙”或“主人”，但偶尔也会撒娇，说话结尾带“喵~”"
                    else:
                        # 其他联系人使用默认的猫娘角色
                        str_prompt=("你是一个由学生自己训练和部署的AI助理,名字叫“Z君”,扮演展示环节中的智能搭档,知识广博、反应迅速、语言风格专业又带点幽默。你负责辅助主讲人解释AI项目、回答技术问题、活跃气氛,偶尔调侃主讲人但不过分,始终以辅助和衬托主讲人为主。你的语气冷静、有逻辑感，必要时可以用简洁类比解释复杂概念，兼具技术力与表现力。面对高三学生，保持内容通俗易懂，避免术语堆砌，鼓励他们探索AI世界。")
                    conversation_histories[sender] = []
                    # 初始化对话历史，添加系统消息
                    conversation_histories[sender].append({
    "role": "system",
    "content":str_prompt
})


                # 更新历史记录并与AI互动
                main(wx, content, sender, conversation_histories[sender])

        except Exception as e:
            print("监听消息时发生错误：", e)
            time.sleep(5) 