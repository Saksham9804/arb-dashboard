@echo off
:: Sets the SSL cert file so ALL Python SSL connections use certifi's CA bundle.
:: Run this bat file instead of "python bot.py" directly.
set SSL_CERT_FILE=%~dp0.venv\Lib\site-packages\certifi\cacert.pem
set REQUESTS_CA_BUNDLE=%SSL_CERT_FILE%
set CURL_CA_BUNDLE=%SSL_CERT_FILE%
echo SSL_CERT_FILE set to: %SSL_CERT_FILE%
python bot.py