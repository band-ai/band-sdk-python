---
name: room-work
description: Coordinate a requester and a worker to deliver reviewed work. Use when a task needs explicit acceptance and completion.
engine: 0.2.0-lab.1
roles: [requester, worker]
---
"""
One requester hands one task to one worker. The worker accepts it to finish the
session; the requester may cancel at any time. Acceptance means the worker took
responsibility for the task, not that the work is done or correct.
"""
contract @infinity => expired

"""
Offer one clear task with what done looks like. Cancel with a reason when the
task is no longer needed.
"""
role requester

"""
Accept the task only if you can do it, and send progress notes while you work.
Say what you could not do instead of going quiet.
"""
role worker

"""
The task, its acceptance criteria and any needed context.
"""
type offer
  task: text(600)

"""
The worker takes responsibility for the offered task it names.
"""
type accept
  task: ref(offer)

"""
A short progress note. It does not change the session state.
"""
type progress
  note: text(200)

"""
Why the requester is withdrawing the task.
"""
type cancel
  reason: text(200)

"""
The requester offers the task.
"""
state new
  """
  Offer the task. The worker then owes one acceptance of it.
  """
  requester -> worker: offer() as task awaits working
    """
    Accept the offered task.
    """
    => accept(task=$task) & complete
     """
     No acceptance arrived within a day of the offer. Offer the task again in a new session.
     """
     | @24h & expired

"""
The worker decides whether to accept the offered task.
"""
state working

"""
In any state, the worker may report progress and the requester may cancel.
"""
anystate
  event worker -> requester: progress()
  requester -> worker: cancel() => cancelled

"""
The worker accepted the task.
"""
end complete

"""
The requester cancelled; stop work on the task.
"""
end cancelled

"""
The session ended without acceptance or cancellation.
"""
end expired
