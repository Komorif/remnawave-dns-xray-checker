(1) Запуск

docker compose up -d --build
docker logs -f cf-xray-sync

(2) Проверка чекера

curl -u admin:'ТВОЙ_ПАРОЛЬ' http://127.0.0.1:2112/api/v1/proxies

