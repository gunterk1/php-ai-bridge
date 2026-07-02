FROM php:8.3-cli

WORKDIR /app

# The official PHP image ships ext-curl and ext-json enabled by default, which is
# all this app needs (it only makes outbound HTTP calls and encodes JSON).
COPY . /app

EXPOSE 8080

# Route every request through the front controller so /api/* is handled in PHP.
CMD ["php", "-S", "0.0.0.0:8080", "-t", "public", "public/index.php"]
