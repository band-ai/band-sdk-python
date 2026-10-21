"""Per-room permission/question lifecycle for the OpenCode adapter.

OpenCode blocks a session mid-turn when its permission rules resolve to
``ask`` (``permission.asked``) or when the model uses the question tool
(``question.asked``); the session resumes only after a reply is POSTed.

One concern per module: ``room`` owns the lifecycle (``RoomApprovals``),
``pending`` the asks outstanding, ``commands`` the reply grammar, ``router``
which ask a room message answers, ``humanwait`` the time parked on a human,
``wording`` every room-visible sentence, ``ports`` what the adapter lends.
"""
