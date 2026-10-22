from interfaces.telegram import TelegramBot


def test_telegram_allowlist():
    bot = TelegramBot(token="test", allowed_user_ids={7})
    received = []
    assert bot.handle_update({"message": {"from": {"id": 7}, "text": "hi"}}, lambda text, user: received.append((text, user)))
    assert not bot.handle_update({"message": {"from": {"id": 8}, "text": "hi"}}, lambda *_: None)
    assert received == [("hi", 7)]
