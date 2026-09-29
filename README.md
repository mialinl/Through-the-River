# Through the River

对话原文的全量记忆库。Ombre 记的是挑过、压过的记忆，River 存的是原话：当时到底谁说了什么。

原文只放在本机，**`data/` 永远不进仓库**（见 `.gitignore`）。

## 现在能做什么

第一步：把 claude.ai 的官方导出拆成一窗一个 json。

```
data/
  raw/       导出原样存档（同一份导两次只存一次）
  windows/   一窗一个文件：日期_窗口名_uuid前8位.json
```

- 删掉的窗口在导出里只剩空壳，直接跳过
- 重 roll 只保留主线（最后生成的那条）
- 正文、thinking、工具调用、工具返回、safety flag 分块存，互不混
- 可以反复导：没变的窗口不动，聊多了的窗口会更新，改过名的窗口不会留两份

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

**以后再导一次新的导出**：同样跑 `python3 -m river split 新导出.zip`，只会动变了的窗口。

## 测试

```bash
python3 -m unittest discover -s tests
```
