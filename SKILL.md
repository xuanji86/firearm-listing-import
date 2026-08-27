---
name: firearm-listing-import
description: Use when importing per-gun photos + descriptions from local "with pictures" folders (each subfolder named after a firearm serial number, holding a description .txt + photos) onto firearms in the POS, and/or publishing those guns to WooCommerce. Also covers first-time setup (generating the Frappe API key, filling mcp/.env), post-listing checks on the WooCommerce admin, and asking the operator whether to send the New Arrivals email to subscribers after a listing run — with a plain-language step-by-step operator guide for non-technical users. Triggers on 把图片/描述更新到枪支, 用 with pictures 文件夹更新枪, 上传序列号图片和描述, 把这些枪 send/推送到 woocommerce/woo 上架, 配置 POS API 密钥上架枪, 带我一步步上架, 发新品邮件/new arrivals 邮件/通知订阅顾客新品, attach firearm photos by serial, list guns on the store, send the new arrivals email. Photos live on the Serial No (per gun), NOT the Item.
---

# Firearm Listing Import（图片+描述 → POS → WooCommerce）

## Overview

把本地 `with pictures` 文件夹里每把枪的照片和描述，写到 POS 对应的 **Serial No** 记录上，并可选地把每把枪作为独立商品上架到 WooCommerce（oldsteelarsenal.com）。上架完成后**问用户要不要给订阅顾客发新品邮件**（见工作流第 5 步）。

文件夹结构：`<root>/<序列号>/` 每个子文件夹名是枪的序列号，里面有 1 个描述 `.txt`（`description.txt` 或编号 txt）和若干照片。`main.*`（或拼错的 `mian.*`）是主图，没有 main 时按文件名排序的第一张为主图。

**描述里的标题**：`.txt` 里如果有一行 `Title: <这把枪的标题>`（一般在 `Specifications` 段里，冒号后可有可无空格），这行就是这把枪在 WooCommerce 上**单独的商品标题**。`attach` 会把它写到该 Serial No 的 `item_name`（Woo 上架时商品名取 `Serial No.item_name`，缺省才回退到共享的 `Item.item_name` 型号名——见 `references/internals.md`），并把这行从描述正文里去掉（免得标题在描述里重复出现）。**没有 `Title:` 行**（旧格式）时不写 `item_name`，商品名照旧用型号名，`resolve` 会标 `NO-TITLE`。

核心工具：`scripts/firearm_listings.py`（子命令 `resolve` / `attach` / `push` / `verify` / `testconn` / `setprice` / `settitle`）。凭据和目标站点都从 `mcp/.env` 读取。

**如何运行**：用 `uv run scripts/firearm_listings.py <子命令>`（脚本带 PEP 723 内联依赖，`uv` 会自动装 `requests`），或 `mcp/.venv/bin/python scripts/firearm_listings.py <子命令>`。**别用裸 `python`**——系统默认解释器没有 `requests`，会 `ModuleNotFoundError`。

> **面向非技术操作者**：如果用户是第一次用、需要配置 API 密钥、问"怎么自己操作 /
> 带我一步步做"，或明显不懂命令行——切换到 `references/operator-guide.md`，照它一段
> 一段带着做，并**代替用户执行命令**（从生成 API key、整理文件夹、定价，到在
> WooCommerce 后台检查/下架）。每步用大白话汇报结果再问是否继续，别甩命令让他自己跑。

## 平台兼容（Claude Code / Codex）

本 skill 在 Claude Code 和 Codex 上行为一致。两条路并存：**gunstore-pos MCP**（读取
和零散小改）+ **可移植脚本 `scripts/firearm_listings.py`**（批量传图/建 gallery/上架，
纯 Python + Frappe REST）。

**skill 发现**
- **源文件只有一份**：repo 的 `.claude/skills/firearm-listing-import/`（受版本控制）。
- **Claude Code**：作为项目 skill 自动发现，无需额外操作。
- **Codex**：只从 `~/.codex/skills/` 或 `~/.agents/skills/` 发现 skill，不读 repo 的
  `.claude/skills/`。已用软链接接过去（一次性，本机已做好）：
  ```bash
  ln -sfn "<repo>/.claude/skills/firearm-listing-import" ~/.codex/skills/firearm-listing-import
  ```
  脚本用 `realpath` 定位 `mcp/.env`，软链接调用也能找到凭据；若是**拷贝**到别处，设
  `FIREARM_ENV=/path/to/gunstore-pos/mcp/.env`。

**gunstore-pos MCP（两端都已注册）**
- Claude Code：repo 根 `.mcp.json` 里的 `gunstore-pos`。
- Codex：`~/.codex/config.toml` 的 `[mcp_servers.gunstore-pos]`（本机已配好，
  `uv run --directory <repo>/mcp gunstore-mcp`；server 自己从 `mcp/.env` 读凭据）。
- 所以 `find_item`、`frappe_*`、`woo_*`、`set_serial_title`、`firearms_in_stock` 等 36 个工具在两端都能用。
  注意 MCP 工具名前缀按各端约定（Claude Code 是 `mcp__gunstore-pos__*`），按名字调用即可。

**该用 MCP 还是脚本**

| 动作 | gunstore-pos MCP（两端都有） | 脚本（任何 agent） |
|---|---|---|
| 测试连接 | `woo_test_connection` / 任意 `frappe_get_document` | `testconn` |
| 查序列号 / 库存 / Item | `find_item`、`frappe_list_documents`、`firearms_in_stock` | `resolve` |
| 定价（`sell_price`） | `frappe_update_document` | `setprice` |
| 设每把枪 Woo 标题（`item_name`） | `set_serial_title` / `frappe_update_document` | `settitle`（或 `attach` 从 `Title:` 行自动写） |
| **按序列号上架 Woo** | ✅ `frappe_run_method` 调 `ffl_woo_sync.woocommerce.client_api.push_serial_now(serial_no)`（**别用** `woo_push_item`——它推该 item_code 下全部兄弟序列号） | ✅ `push`（批量包装**同一个** `push_serial_now`，加长超时 + 跳过已上架/未定价） |
| **按序列号上架 GunBroker** | ✅ `gb_push_serial(serial_no, confirm=true)`（单把；`gb_listing_status` 查状态、`gb_end_listing` 结束） | ✅ `push --channel gunbroker`（批量；**非本地站点须 `FIREARM_ALLOW_PROD=1`**，见下） |
| **传图 + 描述 + 建 gallery** | ❌ 图片字节过不了 MCP（会撑爆上下文） | ✅ `attach`（唯一办法，两个渠道共用同一份图和描述） |
| 查 Woo 商品 | osa-seo `woocommerce-products-list`（仅装了该 MCP 的端，如 Claude Code） | wp-admin 搜 SKU / `curl` WC REST |
| **发 New Arrivals 邮件** | ❌ 不在 POS 侧，MCP 都够不着 | ✅ Woo 站上的 `wp osa-growth new-arrivals …`（走 SSH，见 `references/new-arrivals-email.md`） |

**规则**：
- **按序列号上架** = `push_serial_now`，走 gunstore-pos MCP（`frappe_run_method` 调）**或**
  脚本 `push`——本质同一个调用。单把用 MCP 就行；批量用脚本更省心（MCP 客户端默认
  超时 30s，多图的枪可能不够；脚本用长超时 + 自动跳过已上架/未定价 + canary）。
- **传图 / 建 gallery 必须走脚本**——图片字节进不了 MCP 调用，且脚本负责 resize（关键）。
- 读取和零散小改（查询、定价）两端用 MCP 都顺手。
- Codex 端：读文件用 `shell`（`cat`/`grep`），改文件用 `apply_patch`，跑脚本/`curl` 用
  `shell`，记进度用 `update_plan`。

## 开发（改这个脚本的人看）

`push --channel gunbroker` 的 prod 硬闸有回归测试。**stdlib `unittest`,零依赖零安装**,
不需要 `uv`,也永远不发 HTTP 请求（`requests` 在测试里被打桩,真发请求会直接失败）：

```bash
python3 -m unittest discover -s tests -v
```

覆盖：非本地 BASE 且未设 `FIREARM_ALLOW_PROD` → 拒绝；判不出是否本地（BASE 畸形/无
scheme）→ 按 prod 处理（fail-closed）；`FIREARM_ALLOW_PROD=1` → 放行；子串冒充
（`dev.localhost.evil.example.com`）、userinfo（`http://localhost@evil.example.com/`）、
`[::1]`、尾点 FQDN、大写；以及 `PUSH_CHANNELS` 两个渠道不共用 id 字段。

**改 `cmd_push` / `_is_local_base` / `_prod_gate_blocks` 之前先跑一遍,改完再跑一遍。**
这道闸是"agent 拿着一整个文件夹的枪在跑批"与"真枪上公开拍卖行"之间唯一的东西。

## ⚠️ 安全护栏

1. **`mcp/.env` 指向 PROD**（`https://pos.oldsteelarsenal.com`），Woo 是真实线上店。`attach`/`push` 都是线上写操作、面向顾客、难以撤销。
2. 任何批量写之前，先跑 `resolve`（只读）核对映射，把计划给用户确认。
3. **canary 优先**：先 `attach`/`push` 一把，`verify` + 用户肉眼确认无误，再批量。
4. `push` 会让商品以 `status=publish` **立即上架可购买**；价格取 `Serial No.sell_price`，**0 价会以 $0.00 上架**——脚本默认跳过未定价的枪并报告，让用户先定价。
5. 批量 `attach`/`push` 耗时长（图片上传 + Woo sideload），用 `run_in_background` 跑并记日志。
6. **GunBroker 通道一期只对本地站验证**。`push --channel gunbroker` 打非本地站点会**直接拒绝**，
   除非显式 `FIREARM_ALLOW_PROD=1`。原因不是形式主义：Woo 推错了能取消发布，GunBroker
   推错了是把一把真枪挂上公开拍卖行，买家可以在任何人发现之前拍下，而提前结束 listing
   要人去 GunBroker 站点上手动做。**沙盒还是生产由 POS 的 `GunBroker Settings.sandbox_mode`
   决定，脚本和 MCP 都选不了**——把 `mcp/.env` 指向 `http://dev.localhost:8000` 才是演练的办法。
7. **新品邮件只在用户明确说"发"之后才发**（第 5 步）。它一次发给上百个订阅者、发出去收不回，
   而且是在标题/价格/照片最可能还要改的那几分钟里。**没得到明确同意就不许跑 `send`**；
   用户没提这件事，也要主动问一句——这是上架流程的最后一步，不是可选的礼节。

## 关键数据模型（务必理解，否则会做错）

- 文件夹名 = 序列号 = ERPNext **Serial No** 记录。每把枪的照片/描述/标题属于 **Serial No**（字段 `image` / `image_gallery` / `description` / `item_name`），**不是 Item**。
- **Item 是共享的型号 SKU**：一个 Item（如 `CZ85`、`M1-30`）下挂几十个序列号。多个文件夹可能映射到同一个 Item——所以绝不能把单枪数据写到 Item 上，否则互相覆盖。
- **每把枪的 Woo 标题 = `Serial No.item_name`**：上架时 `ffl_woo_sync` 的 payload builder 用 `Serial No.item_name` 当商品名，为空才回退到 `Item.item_name`（型号名）。所以**给每把枪写各自的 `item_name`（来自 `Title:` 行）才能让它在店里有独立标题**，否则同型号的几十把枪标题全一样。该字段 `fetch_if_empty=1`——一旦写了非空值，之后不会被型号名覆盖；`read_only` 只是后台 UI 限制，REST 照样能写。
- Woo 上架是**按序列号**：每把枪一个 WC 商品，SKU = `item_code::serial`。用 `push_serial_now(serial_no)`，**不要**用 MCP `woo_push_item(item_code)`——那会把该 item_code 下**所有** Active 序列号一起推（如 27 把 CZ85），不只是你处理的那几把。
- 主图：Woo 的 featured image 由 `image_gallery` 中 `is_primary=1` 的那行决定（`_resolve_image_list` 把它放到 `images[0]`）。脚本把主图放在 sort_order 0 且 is_primary 1，并同时写 Serial No 的 `image` 字段。

## 工作流

### 1. resolve（只读，先做）

```bash
uv run scripts/firearm_listings.py resolve --root "/path/to/with pictures N"
```

逐文件夹打印：folder → Serial No、status、price、照片数、主图、FLAGS。重点看 FLAGS：

- `UNRESOLVED` — 该文件夹名找不到 Serial No。文件夹名可能与记录名不一致（大小写 `7408h`→`7408H`、前缀 `14078`→`N14078`、或完全不同 `CR1159`→实际 `SYA1139`）。**问用户**要正确序列号，用 `--map`（JSON `{"CR1159":"SYA1139"}`）补上。
- `FUZZY?` — 文件夹名不是精确匹配，只是恰好唯一地是某个序列号的子串。**不可信**：`attach`/`push`/`setprice` 会拒绝它（防止把单枪数据写错枪）。确认正确序列号后用 `--map` 明确绑定再跑。
- `status=Consumed` 等 — 枪已售/不在库；问用户是否仍要处理（可能刚加回库，再 resolve 一次看是否变 Active）。
- `UNPRICED($0)` — 未定价；`attach` 可以做，但 `push` 会跳过，需用户先定价。
- `has-gallery` / `woo#<id>` — 已经有照片 / 已上架（幂等，默认会跳过）。
- `NO-DESC` — 文件夹缺 .txt。
- `NO-TITLE` — .txt 里没有 `Title:` 行；`attach` 不会写 `item_name`，该枪 Woo 标题仍用共享型号名。新格式建议补 `Title:` 行，或事后用 `settitle` 单独补。

`resolve` 末列 `TITLE` 直接打印解析出的标题——上架前先肉眼核对每把枪的标题（这是顾客看到的商品名）。把 resolve 结果整理成清单给用户确认后再继续。

### 2. attach（写 POS：resize + 上传 + 写 gallery/描述）

```bash
uv run scripts/firearm_listings.py attach --root "/path/..." [--map map.json] [--only SERIAL_A,SERIAL_B] [--force]
```

- **照片必须先 resize**（脚本默认就做：~2000px 长边、JPEG q80，用 `sips`）。**不要上传原图**——原因见下面"为什么 resize"。
- 每把枪：resize 每张图 → 上传到 POS（公开 File，attach 到该 Serial No）→ PUT 设置 `description`（来自 .txt，已去掉 `Title:` 行）+ `image_gallery` + `image`（主图）+ `item_name`（来自 `Title:` 行，没有就不写）。
- `item_name` 就是这把枪在 Woo 上的商品标题；写完后**要 `push`（或重新 push）才会反映到已上架商品**。已上架的枪改了标题，跑 `attach --force` 或 `settitle` 后必须再 `push` 一次。
- 幂等：已有 gallery 的序列号默认跳过；要重做加 `--force`。
- canary：先 `--only 一个序列号`，再 `verify`（会打印 `title=...`），再全量。

### 3. push（上架 Woo，按序列号）

```bash
uv run scripts/firearm_listings.py push --root "/path/..." [--map map.json] [--only ...]
```

- 脚本对每个**已定价 + Active + 未上架**的序列号调用 `push_serial_now`。自动跳过未定价($0)、非 Active、已上架的，并报告。
- **单把上架**也可以直接走 gunstore-pos MCP：`frappe_run_method` 调 `ffl_woo_sync.woocommerce.client_api.push_serial_now`，`kwargs={"serial_no": "<序列号>"}`——和脚本是同一个调用。（**别用** `woo_push_item`，那会推全部兄弟序列号。）
- 商品 `status=publish` 立即上线；价格、库存(1)、描述、resize 后的图（主图为 featured）、category、attributes 都会带上。
- 同样 canary 优先。批量用脚本后台跑（图大时 MCP 30s 可能超时，脚本用长超时）。

### 3b. push --channel gunbroker（上架 GunBroker，按序列号）

```bash
# 演练：把 mcp/.env（或 FIREARM_ENV）指向本地 dev 站
uv run scripts/firearm_listings.py push --channel gunbroker --root "/path/..." --only ONE_SERIAL
```

GunBroker 是第三销售渠道，和 Woo 并列：固定价 Buy Now，价取同一个
`Serial No.sell_price`，图和描述就是 `attach` 已经写好的那份（不用重传）。上架成功
写 `Serial No.gb_item_id`，脚本据此跳过已上架的枪——和 Woo 用 `woo_product_id`
是同一套幂等逻辑。

**开工前先看清楚打的是哪个 GunBroker。** 沙盒还是生产由 POS 的
`GunBroker Settings.sandbox_mode` 唯一决定，脚本这边没有开关。用 MCP 的
`gb_test_connection` 看回包里的 `sandbox` 字段确认一次，再开始。

**非本地站点会被拒绝**：见"安全护栏"第 6 条。真要打生产必须问过用户，
`FIREARM_ALLOW_PROD=1` 显式开，并且**先 canary 一把**：

```bash
FIREARM_ALLOW_PROD=1 uv run scripts/firearm_listings.py push \
    --channel gunbroker --root "/path/..." --only ONE_SERIAL
```

推完立刻核对：脚本打印的 `gb_item_id`，或 `verify --channel gunbroker`；把
`gb_url`/item id 给用户看一眼真实 listing 长什么样，再决定要不要批量。

**结束 listing 不走这个脚本**：用 MCP `gb_end_listing(serial_no, confirm=true)`。
**看返回里的 `confirmed` 而不是 `ok`**——`confirmed=false` 一定带 `pending_manual`
和 `gb_url`，意思是这把枪在 GunBroker 上**还能被买走**，得有人去站点上手动结束。

⚠️ **柜台卖掉自动结束 GB listing 的那条链路目前还没上线**（`serial_channel_exit` hook
在 PR-1 里不存在，`end_listing` 眼下唯一的调用方就是上面这个手动方法）。在 PR-2/PR-3
合入之前：**一把枪在柜台卖掉，就得有人手动 `gb_end_listing`**，否则它在 GunBroker 上
还挂着，可能被再卖一次。这条上线后本段会改回"正常不用管"。

⚠️ **`GUNSTORE_MCP_GUNBROKER_ACTIONS=1` 是部署前置**（lead 裁决，已进 G5 部署清单）。
`gb_push_serial` 和 `gb_end_listing` **是同一个开关**：不设这个变量，MCP 就不注册这两个写工具
（`gb_test_connection` / `gb_listing_status` 不受影响，永远在）。没设的实例上助手会回
"没有这个工具"——**那不是"不用结束"**，是这台机器结束不了，得有人去 GunBroker 站点
手动 **End Item Early**。配机器时别只想着开上架：**能上架却不能结束，正好卡在最危险的
位置上。**

**跳过原因照实读**：守卫拒绝返回 `{"ok": false, "skipped": ..., "message": ...}`，
脚本原样打印。常见的有未定价、非 Active、已上架、被另一个渠道预留（那把枪 Woo
上有人下单了）。重试不会变，按 message 处理。

### 4. verify（核对）

```bash
uv run scripts/firearm_listings.py verify --root "/path/..." [--only ...] [--channel woo|gunbroker]
```

打印每个序列号的 gallery 主图是否对齐、`title`（= `Serial No.item_name`，这把枪的 Woo 商品标题）、
以及该渠道的 listing id（默认 `woo_product_id`；加 `--channel gunbroker` 则看 `gb_item_id`）。Woo 端确认（只上架了你处理的序列号、不是全部兄弟；标题/价格/发布状态/featured 图正确、无重复）：登录 wp-admin 搜 SKU `item_code::序列号`，或 `curl` WC REST；Claude Code 也可用 osa-seo MCP `woocommerce-products-list`（`search_sku "<item_code>::"`）。连接/定价/改标题用脚本的 `testconn` / `setprice` / `settitle` 子命令，不必依赖 MCP。

### 5. new arrivals 邮件（**必须问用户，得到同意才发**）

`verify` 通过之后，这批枪已经在店里可买了。**主动问一句**要不要给订阅"新品提醒"的顾客
发一封 digest——上架流程到这里才算完，别默默结束。

先只读地看一眼，把事实摆给用户，不要凭印象问：

```bash
ssh oldsteel 'cd ~/domains/oldsteelarsenal.com/public_html && wp osa-growth new-arrivals preview'
```

它打印这封信会包含哪几件（含顺序、价格、有没有真照片）、标题行、发给多少人。
然后照这个样子问：

> 刚上架的 6 把已经进了新品邮件队列，这封信会发给 151 个订阅者，标题
> `6 new arrivals just hit the floor`，按价格从低到高排：<列出来>。
> **要现在发吗？** 也可以：先发一封到你邮箱看看 / 换个标题 / 这批不发（清队列）。

拿到明确的"发"之后：

```bash
ssh oldsteel 'cd ~/domains/oldsteelarsenal.com/public_html && wp osa-growth new-arrivals send --yes'
```

`--yes` 是因为 SSH 里没有交互终端；**它跳过的是插件的确认提示，不是用户的确认**——
用户那句"发"必须已经拿到手了。

规则：

- **没得到明确同意就不跑 `send`。** 用户说"先看看"、"等下"、"改完标题再说"，就停在这。
- 用户想先自己看信 → `test --to=<他的邮箱>`（真信，但不动队列、不动统计）。
- 用户只想发这批里的某几把 → `send --only=CZ85::7408H,M1-30::4711 --yes`（认 SKU）。
- 用户要自己写标题 → `send --subject="…" --yes`。
- 用户说这批不值得发（补图、改价、重新上架、测试商品）→ `clear --yes` 把队列清掉，
  否则这些会混进下一封信。
- 发完把回显里的 `Sent N, failed N` 如实报给用户；有 `failed` 或"N product(s) stay
  queued"就一并说明。

一封信最多 8 件，其余留在队列里等下一封——所以一次上架 20 把时，要告诉用户"这封发 8
件，剩下 12 件还在队列里，可以再发一次"。

完整命令、坑、以及没有 SSH 权限时怎么办，见 `references/new-arrivals-email.md`。

## 为什么 resize（被坑过的点）

Woo 创建商品时会从 POS 服务器 **sideload 每张 gallery 图**；POS→Woo 的客户端**硬超时 30s**。手机原图 ~3–4MB / 4000px，几张图 sideload 就超 30s → `ReadTimeout` 500，商品建不出来（且可能产生孤儿/重复）。resize 到 ~2000px/q80（~500–800KB）后 sideload 几秒搞定。**所以 attach 一律先 resize，不要先传原图再补救。**

其它坑见 `references/internals.md`（字段、代码路径、push_serial_now 返回值、超时排查、孤儿商品检查、原图清理）。
