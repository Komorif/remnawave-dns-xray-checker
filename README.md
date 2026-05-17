Этот проект отслеживает скорость ваших узлов, подключенных к подписке remnawave, и выбирает, какие из них будут использоваться для балансировки DNS.

Схема dns балансировки

![[68569035-61d9-4661-974b-ba9014dc5c3c.png]]

Быстрый старт

Настройте .env файл

CF_API_TOKEN - api токен из cloud flare
CF_ZONE_ID - zone id из cloud flare
CF_RECORD_NAME - subdomain на котором будет балансировка

XRAY_CHECKER_USER - имя пользователя от личного кабинета xray cheker
XRAY_CHECKER_PASS - пароль от личного кабинета xray cheker

SUBSCRIPTION_URL - ссылка на подписку системного пользователя из remnawave

```bash
docker compose up -d --build
```

После запуска можете открыть http://your.server.ip:2112 и ввести данные от личного кабинета
Данные от личного кабинета находятся в XRAY_CHECKER_USER, XRAY_CHECKER_PASS

После успешной авторизации вы увидите ваши ноды

![[Pasted image 20260517132117.png]]    