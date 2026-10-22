# Contracts in Markdown

Prose, `inline code`   and  spacing stay exactly as written.

```band
---
name: md-sample
description: A contract inside Markdown.
engine: 0.2.0-lab.1
roles: [lead, worker]
---
"""
Coordinates the offer.
"""
contract @1h => expired

role lead

role worker

type offer

state new
  lead -> worker: offer() => done

end done
```

1. A fragment inside a list item keeps the fence's indentation:

   ```band
   state reviewing
     reviewer -> lead: approve() @2m => complete | stalled
   ```

```text
type   other   fences   are   kept
```

````band
%% A longer fence may hold a line of three backticks.
"""
```
"""
contract @1h => expired
````

```band
"```"
contract   @1h => expired
```

```band
roles:   [lead, worker]
```

```band
event   a -> b: ping()
on worker offline for 5m => paused
```
