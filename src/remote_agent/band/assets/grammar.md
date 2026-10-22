Pinned engine: 0.2.0-lab.1. Author frontmatter source (roles list in header). DSL message fields support text(1..2000), integer(0..1000000), enum(add|multiply), and ref(question). Rules bind request: requester -> calculator: question() as request => answering. A response can require request=$request. State names are strings; end complete is terminal. Durations use @5s and @2m. Add ordinary quoted doc strings above declarations. Don't invent unsupported expressions or arithmetic guards: semantic arithmetic correctness is checked independently by the requester. Here is a valid complete source demonstrating syntax (adapt roles/types to the supplied history):
---
name: hybrid-assistant
description: A remote assistant answers known cases in Python and delegates judgment to an LLM.
engine: 0.2.0-lab.1
roles: [requester, assistant]
---
"""
The room enforces the conversation. A separate remote controller chooses whether
the Python program or LLM answers. Protocol validity does not prove answer quality.
"""
contract @30m mentions 1 => expired

"""
Ask one question at a time and evaluate the answer.
"""
role requester

"""
Answer exact common cases with the program. Delegate open-ended language and
judgment to the LLM; never guess that a fixed rule handles arbitrary English.
"""
role assistant

"""
Ask one self-contained question in plain language.
"""
type question
  text: text(bytes: 1..32000, graphemes: 1..2000)

"""
Answer the question it names. Say so when you are not sure.
"""
type answer
  request: ref(question)
  text: text(bytes: 1..64000, graphemes: 1..4000)

"""
End the conversation. No questions follow.
"""
type stop

"""
No question is open. Ask one, or stop.
"""
state new
  requester -> assistant: question() as request => answering
  requester -> assistant: stop() => complete

"""
A question is open. The assistant owes an answer within five minutes.
"""
state answering
  """
  The assistant owns the deadline regardless of which implementation answers.
  """
  assistant -> requester: answer(request=$request) @5m => ready | stalled

"""
The last question was answered. Ask the next one, or stop.
"""
state ready
  requester -> assistant: question() as request => answering
  requester -> assistant: stop() => complete

"""
The requester stopped the conversation. Send nothing more.
"""
end complete

"""
The assistant did not answer within five minutes. Stop, and tell the requester no answer came.
"""
end stalled

"""
The thirty-minute contract clock ran out. Send nothing more.
"""
end expired

Compiler qualification found: integer ranges require nonnegative bounds; negative integer(-1000..2000) is rejected. Use only nonnegative bounded integers.
