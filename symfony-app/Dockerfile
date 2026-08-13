FROM php:8.4-cli

# pdo_sqlite carries the audit trail; the rest of the stack needs nothing else.
RUN docker-php-ext-install pdo_sqlite

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-interaction --optimize-autoloader

COPY . .
RUN mkdir -p var && chmod -R 777 var

ENV APP_ENV=prod
EXPOSE 8082
CMD ["php", "-S", "0.0.0.0:8082", "-t", "public"]
