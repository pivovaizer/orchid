# Orchid — TODO

## Фундамент
- [x] Инициализация проекта (uv, pyproject.toml)
- [x] Константы pos2 (constants.py)
- [x] Конфигурация (config.py + pydantic)
- [x] CLI (click — start, status)
- [x] Загрузка YAML конфига (loader.py)
- [x] Job модель (job.py)
- [x] PlotManager — запуск процессов (manager.py)
- [x] Персистентность состояния (state.py)
- [x] Scheduler с graceful shutdown (scheduler.py)
- [x] Адаптация под pos2 CLI

## Важное
- [x] Логирование (app log, job stdout/stderr, ротация)
- [x] Архивирование/rsync (перенос плотов на удалённую машину)
- [x] Проверка свободного места на диске (disk.py + orchid dirs)
- [x] Мониторинг процессов через psutil (переживает перезапуск)
- [x] Валидация директорий при старте (с авто-восстановлением)

## Мониторинг и отчёты
- [x] Status — подробный вывод (время работы, прогресс, pid, tmp usage)
- [ ] Interactive dashboard (curses TUI)

## Управление джобами
- [ ] Suspend/Resume отдельных джобов
- [x] Kill по ID (orchid kill <id>)
- [x] Детали джоба (orchid details <id>)
- [x] Просмотр логов джоба (orchid logs <id>)

## Анализ и экспорт
- [ ] Анализ производительности (парсинг логов, время по фазам)
- [ ] CSV экспорт метаданных плотов
- [ ] Статистика (мин, макс, среднее время плоттинга)

## Расширенное
- [ ] Multi-plotter поддержка (bladebit, madmax, pos2)
- [ ] Per-tmpdir overrides в конфиге
- [ ] Приоритизация dst директорий (IO contention, fullness)
- [ ] Генерация конфига (orchid config generate)
