FROM php:8.4-cli

# pdo_sqlite carries the audit trail. The base image ships neither the SQLite
# headers nor unzip, and docker-php-ext-install fails on the former with a
# configure error that does not name the missing package — so both are explicit.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libsqlite3-dev unzip \
 && docker-php-ext-install pdo_sqlite \
 && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-interaction --optimize-autoloader

COPY . .
RUN mkdir -p var && chmod -R 777 var

ENV APP_ENV=prod
EXPOSE 8082
CMD ["php", "-S", "0.0.0.0:8082", "-t", "public"]
