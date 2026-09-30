# Through the River

对话原文的全量记忆库。Ombre 记的是挑过、压过的记忆，River 存的是原话：当时到底谁说了什么。

原文只放在本机，**`data/` 永远不进仓库**（见 `.gitignore`）。

## 结构

```
data/
  raw/                  导出原样存档（同一份导两次只存一次）
  workbench/            工作台：从导出拆出来的窗口，一窗一个文件
  workbench/edits.json  你在工作台上改的东西：编号、名字、说明、藏起来
  windows/              发布区：原文 + 你的修改，合成的完整版本
  river.db              从 windows/ 建的搜索索引，删了也能重建
```

- `import` 接受 zip、解压的文件夹或单个文件，自己认格式：claude.ai 官方导出、Kelivo 备份（`kelivo.db`）
- 每个窗口靠 uuid 认：没变的跳过，聊多了的换成新版本，改过名的不留两份
- 导出里没有了的窗口（app 里删掉的）**不会**跟着删，河里照样留着
- 删掉的窗口在导出里只剩空壳，直接跳过；重 roll 只保留主线
- 正文、thinking、工具调用、工具返回、safety flag 分块存；搜索只搜正文
- 你写的编号和说明记在 edits.json，重新导入不会丢

## 在 Mac 上跑

只需要 Mac 自带的 `python3`（3.9 以上），不用装任何包。

**第一次**

```bash
cd ~
git clone -b claude/full-memory-conversation-json-0eslcs https://github.com/mialinl/Through-the-River.git
cd Through-the-River
python3 -m river import ~/Downloads/导出的文件.zip --label 主号
```

导出的 zip 不用解压；解压出来的文件夹、里面的 `conversations.json` 也都能直接给。格式会自己认，认不出来的不会乱导。
（`import` 以前叫 `split`，旧名字还能用。）
`--label` 是给这个账号起的名字，旧号那份导出用 `--label 旧号`。

**以后更新代码**

```bash
cd ~/Through-the-River
git pull
```

## 日常用法

想更新了，就去 claude.ai 导出一份新的，整个丢进来（不用删旧的、不用挑）：

```bash
python3 -m river import 新导出.zip
```

它会拆到工作台、自动发布、更新索引。

导出里混着不想放进河里的窗口（比如旧号里别的对话）：加 `--hide-new`，这次新进来的窗先全部藏着，
`list` 看一眼，想要的用 `edit 编号或uuid --show` 放出来，再 `publish`。藏着的窗不会发布、搜不到，
以后再导同一份也还是藏着。

其他命令：

```bash
python3 -m river list                          # 工作台里有哪些窗口
python3 -m river edit 15 --note "o55 第一窗"    # 写这一窗的说明
python3 -m river edit 14.5 --name "opus5 第一夜" # 改显示名字
python3 -m river edit 8095ac3a --number 3      # 改编号（用 uuid 开头几位指定窗口也行）
python3 -m river edit 12 --hide                # 藏起来，不发布（--show 取消）
python3 -m river edit 3 5 7 --hide            # 一次藏好几窗（--show 一次放好几窗）
python3 -m river publish                       # 把工作台上的修改发布出去

python3 -m river search 铁盒                    # 搜原文
python3 -m river search 安迷修 字卡              # 几个词同时出现
python3 -m river search 边牧 --window 15 --who claude --from 2026-09-22 --to 2026-09-25
python3 -m river read 15 --at 120              # 读第 15 窗第 120 条前后
python3 -m river read 15 --tail 10             # 读第 15 窗最后 10 条
```

## 接到 claude.ai（MCP）

River 跑在 Mac 的 Docker 里，用自己的 Cloudflare 隧道连到公网，claude.ai 连接器通过 OAuth 密码页授权。
工具有四个：`river_windows`（列窗口）、`river_search`（搜原文）、`river_read`（读一段原文）、`river_tail`（读一窗结尾，开新窗接前情用）。

**1. 在 Cloudflare 建隧道**

Cloudflare 后台 → Zero Trust → Networks → Tunnels → Create a tunnel → 选 Cloudflared → 名字填 `river`。
安装那一步会给一条带 `--token` 的命令，只复制 `--token` 后面那一长串。
下一步 Public Hostname：子域名填 `river`，域名选你的，Service 选 `HTTP`，URL 填 `river:8000`。

**2. 填配置**

```bash
cd ~/Through-the-River
cp .env.example .env
open -e .env
```

填 `RIVER_PUBLIC_URL`（比如 `https://river.你的域名`）、`RIVER_PASSWORD`（自己取一个长密码）、`TUNNEL_TOKEN`（刚才那一长串），保存。

**3. 启动**

```bash
docker compose up -d --build
```

在浏览器打开 `http://localhost:18002/health`，看到 `ok` 就是起来了。

**4. 在 claude.ai 添加连接器**

设置 → 连接器 → 添加自定义连接器 → URL 填 `https://river.你的域名/mcp` → 会跳出 River 的密码页 → 输 `.env` 里的密码。

以后导入新的导出（`python3 -m river import ...`）不用重启容器，索引一更新就能搜到。
更新代码后：`git pull && docker compose up -d --build`。

## 测试

```bash
python3 -m unittest discover -s tests
```
