# siegerpc

Authorized WordPress load testing tools for servers you own or have explicit
permission to test.

`siegerpc` sends XML-RPC POST requests and reports throughput, status codes,
latency percentiles, errors, and bytes received. By default it uses the harmless
`system.listMethods` XML-RPC method, which exercises the XML-RPC endpoint
without requiring credentials or changing WordPress state.

`siegemax` sends Contact Form 7 multipart upload requests with a configurable
file payload and form fields. It is useful for checking upload limits, WAF rules,
PHP worker saturation, and origin behavior under authorized multipart pressure.

## Safety model

This tool is for your own infrastructure only. It requires the
`--i-own-this-server` confirmation flag before it will send traffic.

Defaults are intentionally modest:

- 10 concurrent workers
- 30 second duration
- 50 requests per second maximum
- 30 second request timeout

`siegemax` defaults are stricter because upload requests are heavier:

- 1 concurrent worker
- 30 second duration
- 1 request per second maximum
- 7 MB generated upload file when `--file` and `--file-size-mb` are omitted
- 25 MB upload safety ceiling unless `--allow-large-file` is supplied

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

Contact Form 7 upload test:

```bash
siegemax \
  --url https://example.com/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  --file-size-mb 7 \
  --form-id 50 \
  --unit-tag wpcf7-f50-p30-o1 \
  --i-own-this-server
```

Run from this project directory:

```bash
cd /Users/gurujee/Documents/Playground/siegerpc
python3 -m siegerpc --url https://example.com/xmlrpc.php --i-own-this-server
```

## Examples

### siegerpc

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

### siegemax

Upload an existing 7 MB file to Contact Form 7:

```bash
siegemax \
  --url https://example.com/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  --file /tmp/test7mb.txt \
  --form-id 50 \
  --cf7-version 6.1.6 \
  --unit-tag wpcf7-f50-p30-o1 \
  --duration 60 \
  --rate 1 \
  --i-own-this-server
```

Auto-generate a 10 MB file instead of providing one:

```bash
siegemax \
  --url https://example.com/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  --file-size-mb 10 \
  --form-id 50 \
  --unit-tag wpcf7-f50-p30-o1 \
  --duration 60 \
  --rate 1 \
  --i-own-this-server
```

Increase pressure carefully:

```bash
siegemax \
  --url https://example.com/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  --file /tmp/test7mb.txt \
  --concurrency 3 \
  --rate 3 \
  --duration 120 \
  --timeout 120 \
  --i-own-this-server
```

Override or add form fields:

```bash
siegemax \
  --url https://example.com/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  --field your-email=loadtest@example.com \
  --field custom-field=value \
  --i-own-this-server
```

## Interpreting output

- `availability`: percentage of responses with HTTP status below 500
- `status`: count of HTTP status codes returned by the endpoint
- `latency`: request duration percentiles in milliseconds
- `errors`: connection, timeout, TLS, and other client-side failures

For defensive testing, watch your web server CPU, PHP-FPM workers, database load,
WAF logs, and WordPress access logs while running this tool.
