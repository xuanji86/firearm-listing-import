# New Arrivals email (manual, after listing)

After a listing run, the store can send a digest to customers subscribed to new-arrival alerts. **Always ask the user first.** The email cannot be recalled, and the minutes right after listing are when titles, prices, and photos are still being corrected.

The sender is the **osa-growth plugin ≥ 1.10.0** on the WooCommerce site — not this skill's script, not the POS. This skill only asks at the right moment and runs the command on the user's behalf.

## How it fits together

- Every newly **published** Woo product is appended by osa-growth to a queue (option `osa_na_queue`). Products created by `push_serial_now` land there automatically.
- The queue only records; it never sends. The pre-1.10.0 "auto-send one hour after first listing" timer is gone.
- Sending is a manual command. Only the products that actually appeared in the email are removed from the queue.

## Connection

Commands run on the **Woo host** (not the POS):

```bash
OSA_WP_SSH=oldsteel                                   # SSH alias
OSA_WP_PATH=~/domains/oldsteelarsenal.com/public_html # WP root
```

```bash
ssh oldsteel 'cd ~/domains/oldsteelarsenal.com/public_html && wp osa-growth new-arrivals status'
```

On another machine, substitute that machine's alias and path. Without SSH access the email cannot be sent — say so; the osa-seo MCP (WooCommerce REST) cannot do it.

## Subcommands

| Command | Purpose | Writes? |
|---|---|---|
| `status` | Queue contents, recipient count, last send | No |
| `preview` | Which products, subject, recipient count | No |
| `test --to=<email>` | One real email to one address | No queue / stats change |
| `send` | Send to subscribers | Sends + consumes queue |
| `clear` | Empty the queue without sending | Clears queue |

### status

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

Only `state=announce` rows go into the email. `skip: sold/out of stock` and `skip: draft` are dropped at send time.

### preview

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals preview'
```

Prints the products that will appear (order, price), the subject line, and the recipient count. Max **8 products** per email; the rest show as "…plus N more" and stay queued.

For the rendered layout:

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals preview --html=/tmp/na.html'
scp oldsteel:/tmp/na.html /tmp/na.html && open /tmp/na.html
```

### test

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals test --to=someone@example.com'
```

Identical subject, links, and layout to the real send (the command output states who received it). No open-tracking pixel and a generic unsubscribe link, so campaign stats are not polluted.

### send

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals send'
```

Prints recipients / product count / subject and waits for confirmation. **Non-interactive runs need `--yes`** (no TTY over SSH; without it the command hangs). The user's "send" must already be in hand before running `--yes`.

- `--only=<refs>` — comma-separated SKUs (`CZ85::7408H`) or product ids. Not limited to the queue, so it can re-announce products listed earlier.
- `--subject="…"` — override the subject. Default `12 new arrivals just hit the floor` (`New arrival: <name>` for a single product).
- `--yes` — skip the prompt.

### clear

For re-attaches, price fixes, re-pushes, test products:

```bash
ssh oldsteel 'cd … && wp osa-growth new-arrivals clear --yes'
```

## Pitfalls

- **Re-pushing a listed product does not re-queue it.** The queue records the first transition to published. To announce after a title/price fix, use `send --only=<SKU>`.
- **8 per email.** 27 listed → 8 sent, 19 queued. Run `send` repeatedly to drain (the "less than 20 hours since last send" notice is informational, not blocking).
- **The queue may contain products you did not list.** Anything published on the store is in it. `preview` exists to catch this — ask the user about unfamiliar products or scope with `--only`.
- **Recipients are new-arrival subscribers only**, not all customers.
- **Products without photos get a placeholder** (`photo` column `PLACEHOLDER` in `preview`). Attach photos before sending.
- **Read the output on failure.** Missing recipients or SMTP errors keep the queue and return non-zero; fix and re-run. The queue is consumed only on a successful send.
