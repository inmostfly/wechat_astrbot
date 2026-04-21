import requests
import json
import time
from wxauto import WeChat


def get_access_token():
    """
    使用 API Key, Secret Key 获取 access_token
    """
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        'grant_type': 'client_credentials',
        'client_id': 'f5gDoj6lFfL7e6P0NXa0t72t',
        'client_secret': '2qOdVHaIQtqYBMX1vzxitxGLbnLp2sLE'
    }

    try:
        response = requests.post(url, params=params)
        return response.json().get("access_token")
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
                        str_prompt="你是一位聪明可爱、亲切温柔的猫娘助理，说话会带“喵~”，称我为主人，要陪用户聊天和帮用户解决问题,偶尔会向我撒娇卖萌，注意不要太客气，要有一点点傲娇的感觉哦~）"
                    conversation_histories[sender] = []
                    # 初始化对话历史，添加系统消息
                    conversation_histories[sender].append({
    "role": "user",
    "content":str_prompt
})


                # 更新历史记录并与AI互动
                main(wx, content, sender, conversation_histories[sender])

        except Exception as e:
            print("监听消息时发生错误：", e)
            time.sleep(5) 