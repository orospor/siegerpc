# siegerpc

Authorized WordPress `xmlrpc.php` load tester for simulating XML-RPC pressure
against servers you own or have explicit permission to test.

`siegerpc` sends XML-RPC POST requests and reports throughput, status codes,
latency percentiles, errors, and bytes received. By default it uses the harmless
`system.listMethods` XML-RPC method, which exercises the XML-RPC endpoint
without requiring credentials or changing WordPress state.

## Safety model

This tool is for your own infrastructure only. It requires the
`--i-own-this-server` confirmation flag before it will send traffic.

Defaults are intentionally modest:

- 10 concurrent workers
- 30 second duration
- 50 requests per second maximum
- 30 second request timeout

## Quick start

Install from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | bash
```

Global system install on Debian/Ubuntu:

```bash
curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | sudo bash -s -- --system
```

If your Python environment needs a user-level install:

```bash
curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | bash -s -- --user
```

```bash
siegerpc --url https://example.com/xmlrpc.php --i-own-this-server
```

Run from this project directory:

```bash
cd /Users/gurujee/Documents/Playground/siegerpc
python3 -m siegerpc --url https://example.com/xmlrpc.php --i-own-this-server
```

## Examples

Moderate test for one minute:

```bash
python3 -m siegerpc \
  --url https://example.com/xmlrpc.php \
  --duration 60 \
  --concurrency 25 \
  --rate 100 \
  --i-own-this-server
```

Fixed request count instead of duration:

```bash
python3 -m siegerpc \
  --url https://example.com/xmlrpc.php \
  --requests 1000 \
  --concurrency 20 \
  --rate 80 \
  --i-own-this-server
```

Use a custom XML-RPC method:

```bash
python3 -m siegerpc \
  --url https://example.com/xmlrpc.php \
  --method demo.sayHello \
  --i-own-this-server
```

Save CSV results:

```bash
python3 -m siegerpc \
  --url https://example.com/xmlrpc.php \
  --csv results.csv \
  --i-own-this-server
```

## Interpreting output

- `availability`: percentage of responses with HTTP status below 500
- `status`: count of HTTP status codes returned by the endpoint
- `latency`: request duration percentiles in milliseconds
- `errors`: connection, timeout, TLS, and other client-side failures

For defensive testing, watch your web server CPU, PHP-FPM workers, database load,
WAF logs, and WordPress access logs while running this tool.
