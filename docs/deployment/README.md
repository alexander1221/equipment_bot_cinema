# Развёртывание и сопровождение

Полная инструкция для студентов и сопровождающих. Версия 1.1 от 09.11.2026.

- [Читать инструкцию на GitHub](deployment-guide.md)
- [Скачать PDF](https://github.com/alexander1221/equipment_bot_cinema/raw/refs/heads/main/docs/deployment/deployment-guide.pdf)
- [Скачать Word](https://github.com/alexander1221/equipment_bot_cinema/raw/refs/heads/main/docs/deployment/deployment-guide.docx)
- [Скачать полный комплект ZIP](https://github.com/alexander1221/equipment_bot_cinema/raw/refs/heads/main/docs/deployment/equipment-bot-deployment-kit.zip)
- [Скрипт резервного копирования](equipment-backup.py)
- [Контрольные суммы SHA-256](SHA256SUMS.txt)

Руководство охватывает Ubuntu 24.04, Docker Compose, nginx и HTTPS, Telegram и прокси, управление контейнером, ежедневные резервные копии, восстановление, обновление, домен и диагностику.

Для существующего сервера сначала прочитайте разделы 1–3 и 13. Повторять установку и перезаписывать действующие настройки не нужно. Текущее состояние сервера зафиксировано на дату документа и требует проверки перед изменениями.

Скрипт копирования рассчитан на описанные серверные пути. Запускайте его только по инструкции. Полученные копии содержат секреты и персональные данные — публиковать их в репозитории нельзя.

Если браузер открывает файл вместо скачивания, на странице файла GitHub нажмите **Download raw file**. ZIP содержит PDF, Word, Markdown, скрипт, описание и контрольные суммы.
