from wxauto4 import WeChat

wx = WeChat(ads=False)

# 发送消息
who = "Inmost"
# # 获取当前聊天页面（文件传输助手）消息，并自动保存聊天图片
# msgs = wx.GetAllMessage(savepic=True)
# for msg in msgs:
#     print(f"{msg[0]}: {msg[1]}")
#第二次测试
wx.SendMsg('hello again', who)


print('wxauto测试完成!')
