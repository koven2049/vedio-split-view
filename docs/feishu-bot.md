# 飞书机器人：丢链接即分析

在飞书里把 YouTube / B 站 / 小宇宙链接发给「视频分析」机器人，后台走现有分析流水线，完成后推回卡片。

这不是群自定义 webhook 机器人（那种只能往外推）。必须是**企业自建应用 + 机器人能力**。

## 使用

1. 飞书搜索机器人，或把它拉进一个小群。
2. 私聊直接粘贴链接；群里要 `@机器人` 再贴链接。
3. 立刻收到「已开始」；跑完再收到结果卡，点「查看全文」打开免登录全文页，并额外回一句 `ok`。
4. 超长视频会要你点「确认继续」。
5. 网页上点「分析」完成时，也会给白名单里的人私聊一句 `ok`（飞书 `enabled` 且凭证配好时）。

未在白名单里的人会被回复「未授权」。

## 开放平台（做一次）

1. 打开 [飞书开发者后台](https://open.feishu.cn/app)，创建**企业自建应用**。
2. 应用能力 → 添加 **机器人**。
3. 权限管理，开通应用身份权限：
   - `im:message.p2p_msg:readonly`（收私聊）
   - `im:message.group_at_msg:readonly`（收群里 @）
   - `im:message:send_as_bot`（以机器人发消息）
4. 事件与回调 → 订阅方式选 **将事件发送至开发者服务器**（不要长连接）。
   - 请求地址：`https://<你的域名>/api/hooks/feishu`
   - 添加事件 `im.message.receive_v1`
   - 添加回调 `card.action.trigger`（超长视频确认按钮）
5. 保存后飞书会 POST challenge；服务启用且凭证配好后握手才会过。
6. 版本管理 → 创建版本并发布给本企业。可见范围只加要用的几个人。
7. 在飞书里打开机器人，发一条自己的私聊，从服务日志或事件里抄 `open_id`，写入 `app.yaml` 的 `feishu.allowed_open_ids`。

凭证（App ID / Secret、Verification Token、Encrypt Key）写入 `config/feishu.yaml`，不要写进 `app.yaml`，不要提交 git。

## 本仓库配置

`config/app.yaml`（非密钥）：

```yaml
feishu:
  enabled: true
  result_base_url: "https://your-host.example"   # 结果卡「打开笔记」的站点根
  allowed_open_ids:
    - ou_xxxxxxxx
  secrets_file: feishu.yaml
```

`config/feishu.yaml`（从 `config/feishu.yaml.example` 复制，权限必须 0600）：

```yaml
app_id: cli_xxx
app_secret: "..."
verification_token: "..."
encrypt_key: "..."          # 强烈建议配置；配了之后飞书会加密推送
```

`app_secret` / `verification_token` / `encrypt_key` 落盘时会自动改成 `enc:v1:` 密文。KEK 在 `config/secret.key`（自动生成，0600，git / rsync 均排除）。新机器要重新录入 `feishu.yaml`，不要复制 KEK。

改完后端代码后：`./manage.sh rebuild`（容器未挂载 src）。
