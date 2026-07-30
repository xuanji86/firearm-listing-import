# New Arrivals 邮件（上架后手动发）

上架完成后，网店可以给订阅"新品提醒"的顾客群发一封 digest。**这一步永远是问过用户
之后才做的**——邮件发出去撤不回来，而上架刚结束的那几分钟恰恰是标题、价格、照片还在
改的时候。

发送方是 WooCommerce 站上的 **osa-growth 插件 ≥ 1.10.0**（不是本 skill 的脚本，也不
是 POS）。本 skill 只负责在正确的时机**问**，然后**替用户跑那条命令**。

---

## 它是怎么串起来的

- Woo 上**每新建一个 published 商品**，osa-growth 就把它的 post id 记进一个队列
  （option `osa_na_queue`）。`push_serial_now` 建的每把枪都会自动进队列，不用做任何事。
- 队列**只是记录**，本身不会发信。1.10.0 之前有个"首件上架 1 小时后自动发"的定时器，
  已经关掉了——就是因为那一小时里东西还在改。
- 发信是一条要人来敲的命令。发完之后，**只有真正出现在邮件里的那几件**会被移出队列。

## 连接方式

命令跑在 **Woo 站**上（不是 POS）。本机的值：

```bash
OSA_WP_SSH=oldsteel                                   # SSH 别名
OSA_WP_PATH=~/domains/oldsteelarsenal.com/public_html # WP 根目录
```

所以一条命令长这样：

```bash
ssh oldsteel 'cd ~/domains/oldsteelarsenal.com/public_html && wp osa-growth new-arrivals status'
```

> 换一台机器就换成那台的 SSH 别名和路径；没有 SSH 权限就没法发信，如实告诉用户，
> 别试图绕（osa-seo MCP 是 WooCommerce REST，发不了这封信）。

---

## 五条子命令

| 命令 | 作用 | 会不会改东西 |
|---|---|---|
| `status` | 队列里有什么、收件人多少、上次发了什么 | 只读 |
| `preview` | 这封信会包含哪几件、什么标题、发给多少人 | **不改任何东西** |
| `test --to=<邮箱>` | 往一个邮箱发一封真信 | 不动队列、不动统计 |
| `send` | **真发**给订阅者 | 发信 + 消费队列 |
| `clear` | 清空队列但不发信 | 只清队列 |

### status —— 先看清楚要发什么

```bash
ssh oldsteel 'cd ~/domains/oldsteelarsenal.com/public_html && wp osa-growth new-arrivals status'
```

```
Automatic sending: OFF — send by hand
Last digest sent:  never

Queue: 12 product(s)
id	sku	name	price	state
895	CZ85::7408H	CZ 85 9mm …	599.00	announce
…
12 announceable — the next send shows 8 and leaves 4 queued for the send after.

Recipients: 3 subscribed contact(s) tagged "new-arrivals".
```

`state` 列要看：`announce` 才会进邮件，`skip: sold/out of stock`、`skip: draft`
是已经卖掉或没发布的，发信时会被自动清掉。

### preview —— 把邮件内容摊开给用户看

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals preview'
```

打印将出现在邮件里的**那几件**（含顺序和价格）、标题行、收件人数。
一封信最多放 **8 件**，其余显示成一行"…plus N more"，**并留在队列里等下一封**。

要看真正的排版就加 `--html`，然后取回本地打开：

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals preview --html=/tmp/na.html'
scp oldsteel:/tmp/na.html /tmp/na.html && open /tmp/na.html
```

### test —— 往用户自己的邮箱先发一封

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals test --to=someone@example.com'
```

主题、链接、排版都跟真信**一模一样**（所以收到时别误以为已经群发了——命令回显会写明
只发给了谁）。它不带打开追踪像素、退订链接是通用的，因此不会污染这封 campaign 的
打开率统计。

### send —— 真发

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals send'
```

会先把"发给 N 人 / 共 M 件 / 标题是什么"打出来再等确认。**非交互执行必须加 `--yes`**
（SSH 里没有 tty，不加会挂住）——所以在跑 `--yes` 之前，用户的"发"必须已经拿到了。

常用参数：

- `--only=<refs>`：只发指定的几件，逗号分隔，**支持 SKU**（`CZ85::7408H`），也支持
  商品 id。不受队列限制——可以补发上周就上架的东西。
- `--subject="…"`：换标题行。默认是 `12 new arrivals just hit the floor`
  （只有一件时是 `New arrival: <商品名>`）。用户对标题有要求就用这个。
- `--yes`：跳过确认（非交互必需）。

### clear —— 这批不值得发

补图、改价、重新上架、测试商品——这类不该惊动顾客的，清掉队列：

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals clear --yes'
```

---

## 会踩的坑

- **重新 push 已上架的商品不会再进队列**。队列只在商品**第一次**变成 published 时记录。
  改完标题/价格重新 `push` 之后想announce，用 `send --only=<SKU>`。
- **一封最多 8 件**。上架 27 把 → 第一封发 8 件、19 件留在队列。想全发就连着跑几次
  `send`（每次都会提示"距上次发送不足 20 小时"，那只是提醒，不阻拦）。
- **队列可能不止你刚上架的东西**。店里从别的途径发布的商品也在里面。`preview` 就是
  用来发现这件事的——列表里有陌生商品就问用户，或者用 `--only` 圈定。
- **收件人是订阅"新品提醒"的人**，不是全体顾客。想群发全体客户是另一件事，不走这条命令。
- **没有照片的商品会用占位图**。`preview` 的 `photo` 列标 `PLACEHOLDER` 就是这种；
  遇到先把图 attach 好再发，不然邮件里是一块灰底。
- **失败一定要看回显**。收件人查不到、SMTP 挂了这类失败会**保留队列**并返回非零退出码，
  修好再跑一次就行，不会丢东西。真发出去了才会消费队列。
