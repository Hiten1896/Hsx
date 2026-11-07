from api.chat import ChatStreamRegistry


def test_chat_stream_assigns_event_ids_and_replays_after_reconnect():
    streams = ChatStreamRegistry()
    initial_queue = streams.register("job-1")
    streams.emit("job-1", "message_start", {"run_id": "job-1"})
    streams.emit("job-1", "text_delta", {"text": "first"})
    first = initial_queue.get_nowait()
    second = initial_queue.get_nowait()
    assert (first.id, first.event) == (1, "message_start")
    assert (second.id, second.event) == (2, "text_delta")

    streams.remove("job-1", initial_queue)
    streams.emit("job-1", "text_delta", {"text": "second"})
    streams.emit("job-1", "done", {})
    resumed_queue, replay, finished = streams.subscribe("job-1", after_event_id=2)

    assert [event.id for event in replay] == [3, 4]
    assert [event.event for event in replay] == ["text_delta", "done"]
    assert finished is True
    assert resumed_queue.empty()


def test_chat_stream_subscription_receives_live_events_and_detaches():
    streams = ChatStreamRegistry()
    original_queue = streams.register("job-2")
    active_queue, replay, finished = streams.subscribe("job-2", after_event_id=0)

    streams.emit("job-2", "job", {"state": "running"})

    assert replay == []
    assert finished is False
    event = active_queue.get_nowait()
    assert (event.id, event.event, event.data) == (1, "job", {"state": "running"})
    assert original_queue.get_nowait().event == "job"
    streams.remove("job-2", active_queue)
    streams.emit("job-2", "done", {})
    assert active_queue.empty()
    assert original_queue.get_nowait().event == "done"
