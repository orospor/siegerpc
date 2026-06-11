# siegerpc

Orospor Labs WordPress resilience tester for authorized XML-RPC and Contact
Form 7 capacity checks.

The project ships two command-line tools:

- `siegerpc`: sends XML-RPC POST requests and reports throughput, status codes,
  latency percentiles, errors, and bytes received.
- `siegemax`: sends Contact Form 7 multipart upload requests with configurable
  files and form fields.

Both tools are designed for defensive validation on systems you own or have
explicit permission to test.

## Responsible use

These tools can create real load. Use them only in approved test windows and
only against infrastructure in scope.

The commands require the confirmation flag:

```bash
--i-own-this-server
```

Defaults are intentionally conservative. Increase concurrency, rate, duration,
or upload size only after watching server health and confirming the environment
can absorb the test.

## Install

Install from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | bash
```

System install on Debian or Ubuntu:

```bash
curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | sudo bash -s -- --system
```

User-level install:

```bash
curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/install.sh | bash -s -- --user
```

Run from a local clone:

```bash
git clone https://github.com/orospor/siegerpc.git
cd siegerpc
python3 -m pip install -e .
```

## `siegerpc`: XML-RPC tester

By default, `siegerpc` uses the harmless XML-RPC method
`system.listMethods`. This exercises the endpoint without credentials and
without changing WordPress state.

Basic check:

```bash
siegerpc --url https://example.com/xmlrpc.php --i-own-this-server
```

One-minute test:

```bash
siegerpc \
  --url https://example.com/xmlrpc.php \
  --duration 60 \
  --concurrency 25 \
  --rate 100 \
  --i-own-this-server
```

Fixed request count:

```bash
siegerpc \
  --url https://example.com/xmlrpc.php \
  --requests 1000 \
  --concurrency 20 \
  --rate 80 \
  --i-own-this-server
```

Custom XML-RPC method:

```bash
siegerpc \
  --url https://example.com/xmlrpc.php \
  --method demo.sayHello \
  --i-own-this-server
```

Save per-request CSV:

```bash
siegerpc \
  --url https://example.com/xmlrpc.php \
  --csv results.csv \
  --i-own-this-server
```

## `siegemax`: Contact Form 7 upload tester

`siegemax` sends multipart Contact Form 7 feedback requests. It is useful for
checking upload limits, CDN/WAF handling, PHP worker saturation, and origin
behavior under authorized multipart pressure.

Basic generated upload:

```bash
siegemax \
  --url https://example.com/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  --form-id 50 \
  --unit-tag wpcf7-f50-p30-o1 \
  --file-size-mb 7 \
  --i-own-this-server
```

Upload an existing file:

```bash
siegemax \
  --url https://example.com/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  --file /tmp/test7mb.txt \
  --form-id 50 \
  --unit-tag wpcf7-f50-p30-o1 \
  --duration 60 \
  --rate 1 \
  --i-own-this-server
```

Run until interrupted:

```bash
siegemax \
  --url https://example.com/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  --file-size-mb 10 \
  --form-id 50 \
  --unit-tag wpcf7-f50-p30-o1 \
  --rate 1 \
  --forever \
  --i-own-this-server
```

Carefully increase pressure:

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

## Default safety limits

`siegerpc` defaults:

- 10 concurrent workers
- 30 second duration
- 50 requests per second maximum
- 30 second request timeout

`siegemax` defaults:

- 1 concurrent worker
- 30 second duration
- 1 request per second maximum
- 7 MB generated upload file when no file is supplied
- 25 MB upload safety ceiling unless `--allow-large-file` is supplied

## Reading output

The summary includes:

- `availability`: responses with HTTP status below 500
- `status`: count of HTTP status codes returned by the endpoint
- `latency`: min, mean, p50, p95, p99, and max request duration
- `errors`: connection, timeout, TLS, and client-side failures
- `bytes read`: total response bytes received

During a defensive test, watch:

- Web server CPU and memory
- PHP-FPM or LiteSpeed worker occupancy
- CDN cache and WAF logs
- WordPress access and error logs
- Database load and slow queries

## WordPress hardening helper

The repository includes a defensive helper script:

```bash
curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/scripts/harden-wordpress-forms.sh \
  | sudo bash -s -- --wp-root /var/www/html --max-upload-mb 8 --max-requests 10 --window-seconds 60
```

For Nginx, pass the site config so rules can be added before PHP sees the
request:

```bash
curl -fsSL https://raw.githubusercontent.com/orospor/siegerpc/main/scripts/harden-wordpress-forms.sh \
  | sudo bash -s -- --wp-root /var/www/html --nginx-site /etc/nginx/sites-available/default --max-upload-mb 8 --max-requests 10 --window-seconds 60
```

The helper can install:

- A WordPress MU-plugin that blocks `xmlrpc.php`
- Per-IP rate limiting for Contact Form 7 feedback REST requests
- Upload body size checks for Contact Form 7 feedback requests
- Optional Nginx rules to block or rate-limit before PHP

## Defensive questions to answer

- Is XML-RPC still exposed?
- Are XML-RPC calls rate-limited before PHP work begins?
- Are Contact Form 7 upload limits enforced consistently?
- Does the CDN/WAF block abusive POST patterns at the edge?
- How quickly do users see 5xx responses when workers saturate?
- Which mitigation gives the best protection with the least user friction?

## License

Use this project responsibly under the license terms published in this
repository.
