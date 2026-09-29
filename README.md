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
python3 -m river split ~/Downloads/导出的文件.zip --label 主号
```

导出的 zip 不用解压，直接给 zip 也行，给里面的 `conversations.json` 也行。
`--label` 是给这个账号起的名字，旧号那份导出用 `--label 旧号`。

**以后更新代码**

```bash
cd ~/Through-the-River
git pull
```

## 日常用法

想更新了，就去 claude.ai 导出一份新的，整个丢进来（不用删旧的、不用挑）：

```bash
python3 -m river split 新导出.zip
```

它会拆到工作台、自动发布、更新索引。其他命令：

```bash
python3 -m river list                          # 工作台里有哪些窗口
python3 -m river edit 15 --note "o55 第一窗"    # 写这一窗的说明
python3 -m river edit 14.5 --name "opus5 第一夜" # 改显示名字
python3 -m river edit 8095ac3a --number 3      # 改编号（用 uuid 开头几位指定窗口也行）
python3 -m river edit 12 --hide                # 藏起来，不发布（--show 取消）
python3 -m river publish                       # 把工作台上的修改发布出去

python3 -m river search 铁盒                    # 搜原文
python3 -m river search 安迷修 字卡              # 几个词同时出现
python3 -m river search 边牧 --window 15 --who claude --from 2026-09-22 --to 2026-09-25
python3 -m river read 15 --at 120              # 读第 15 窗第 120 条前后
python3 -m river read 15 --tail 10             # 读第 15 窗最后 10 条
```

## 测试

```bash
python3 -m unittest discover -s tests
```
