# Synthetic chat used to exercise contract creation

This is a test fixture, not a recovered private conversation. Message IDs make the source of each rule inspectable. `lead`, `writer`, and `reviewer` are three distinct participants.

**H01 — lead:** Please draft a release note. I will send an offer describing the work to the writer and reviewer. The writer must accept or refuse the exact offer before starting. If no acceptance arrives within two minutes, call it unclaimed.

**H02 — writer:** Agreed. A refusal includes a reason. After accepting, I will submit a draft reference within fifteen minutes. Each submission refers to its offer.

**H03 — reviewer:** Send the draft to me and the lead. I will independently approve the exact submission or request changes, with a reason, within five minutes. A changes request ends this session as needs_revision; a new offer starts another session.

**H04 — lead:** Approval does not finish the work. I must accept the reviewed submission within five minutes. Only that acceptance makes the outcome complete. Missing submission, review, or final acceptance is stalled.

**H05 — writer:** Could I approve my own draft and mark it done to save time?

**H06 — lead:** No. The reviewer is a different participant, and my acceptance is still required. A repeated delivery of a message must not count as another contribution.

**H07 — lead:** Use a thirty-minute total lifetime and a five-minute inactivity timeout. Only a state transition resets inactivity. Allow at most two recipients per message, thirty mentions per session, and twenty accepted messages. These are values for this example.

**H08 — reviewer:** A submitted reference is a claim. I need to inspect that exact artifact before approving it. The protocol cannot establish the quality of the writing by checking its identifier.

**H09 — lead:** There are no external tool effects in this example. I can cancel any active session; that ends it as cancelled. A timeout of either whole-session timer ends it as expired.

The transcript does not specify authentication, artifact storage, or transport delivery guarantees. Those remain host responsibilities. H05 is a rejected suggestion, not a contract rule.
