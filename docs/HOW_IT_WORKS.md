# How UnifiOptimizer works

UnifiOptimizer watches a UniFi network the way a good technician would. It keeps a
running history, notices when something starts misbehaving, waits to rule out
the obvious false alarms, tells you what it thinks is wrong and why, and then
checks whether the fix held. The three figures below walk through that, from
the wire up to the number on the dashboard. Every name in them is made up; no
real device, address, or room appears.

If you want the full design, with the schema, the detector catalog, and the
thresholds, that lives in [`ARCHITECTURE.md`](ARCHITECTURE.md). This page is the
plain-language version.

The whole thing turns on one idea. A UniFi controller throws away its
fine-grained stats after about a day. Anything you do not collect daily is
simply gone, so a tool that runs once and prints a report can never tell you
that a port started erroring on Tuesday or that a mesh link has been sliding for
a week. UnifiOptimizer can, because it was watching and it wrote everything down.

## 1. How UnifiOptimizer watches

![End-to-end flow: the UniFi controller feeds a 60-second collector and active
probes, everything lands in one SQLite store, detectors and the health model
read from the store and feed the issue engine, which reaches you through the web
UI and Home Assistant. A fix loops back to the controller only when you approve
it.](img/how-netadmin-watches.png)

Read it left to right. On the left is the UniFi controller, which UnifiOptimizer talks
to two ways at once. It pulls device, client, and health stats over the REST API
every 60 seconds, and it listens to the controller's event WebSocket so things
like roams and port events arrive the moment they happen. Alongside those, a
small prober runs its own DNS and ICMP timing, because the controller reports no
DNS or DHCP timing at all and that gap is where a lot of "the internet feels
slow" actually lives.

Everything flows into the middle box, and the middle box is the point of the
whole design. It is one SQLite file. It is the memory. History goes in as raw
samples for about a month, then rolls up to hourly for eighteen months and daily
after that, so a comparison against this time last year stays possible without
keeping every five-minute reading forever. Two details there matter more than
they look. Counters like error counts are stored as per-interval rates rather
than the ever-climbing raw number, so a reboot does not read as a billion new
errors. And a gap in collection is recorded as a gap, never backfilled with
zeros, because "we could not reach the controller" and "everything was fine" are
different facts and a detector needs to tell them apart.

From the store, two readers work in parallel. The detectors apply plain,
inspectable rules: static thresholds plus bands drawn from each series' own
recent history. When one fires, it also writes down which false alarms it
checked first. A cable alert that trips on a gigabit port stuck at 100 Mbps
records that it confirmed the port is genuinely gigabit-capable and that the
attached device is not a known 100 Mbps class. That audit trail is the
difference between an admin and an alarm generator. The second reader is the
health model, which scores every active client-minute as pass or fail; figure 3
is entirely about how that works.

Both readers feed the issue engine, which tracks each problem through its life,
and the issue engine is what reaches you: a local web app and API, and
optionally Home Assistant over MQTT for phone notifications. One arrow runs
backward, from the issue engine to the controller, and it is dashed on purpose.
UnifiOptimizer can propose a config fix and render the exact change, but it applies
nothing on its own. That arrow only carries current when you click.

## 2. The life of an issue

![State path for a single issue: many findings collapse into one fingerprint,
which moves through pending, active, resolving, and resolved. A refire within a
day reopens the same row, a fire during resolving snaps back to active, and a
bigger fault mutes the smaller issues underneath it.](img/life-of-an-issue.png)

A stateless tool alerts every time it looks and sees the problem, which trains
you to ignore it. UnifiOptimizer does the opposite, and figure 2 is how.

Every poll that still sees the problem re-emits a finding. Those findings do not
each become an alert. They collapse into a single fingerprint, a hash of the
detector, the device, and the specific dimensions of the fault. One open issue
exists per fingerprint, so the tenth time a cable errors this hour, UnifiOptimizer
updates the one issue it already has rather than opening a tenth.

That issue moves through four states. It starts in **pending**, seen but not yet
trusted, and it has to hold for a few consecutive polls before it is promoted.
That debounce is what keeps a single noisy reading from paging you. Once it
holds, it becomes **active**, and this is where the "network admin that
remembers" line earns itself: the issue carries a clock. "Still broken, day 5"
is just now minus the moment it was first seen. When the problem stops, the
issue moves to **resolving** and has to stay clean for several more polls before
it is marked **resolved**. If it fires again while resolving, it snaps straight
back to active; a problem that clears for one poll and returns has not resolved.

Two loops make it behave like a person rather than a state machine. If a resolved
issue fires again within a day, UnifiOptimizer reopens the same row instead of
spawning a fresh one, so a flapping fault reads as one recurring problem with a
history, not a stream of new tickets. And when a big fault would obviously cause
a pile of small ones, the big fault mutes them. A downed switch suppresses its
own ports' error and flapping issues for as long as it is down, because those
ports being unreachable is not new information while the switch is off. When the
switch is unreachable, UnifiOptimizer also stops advancing anyone's "resolved" clock:
not seeing a problem is not the same as the problem being fixed.

## 3. Health as user-minutes

![A grid of clients by five-minute buckets. Each active client-minute is a pass
or a fail. One failed minute is pinned to a single cause on a single device. An
idle client with bad signal contributes zero failed minutes. Adding up the
failed minutes is the score, and the breakdown is the same
query.](img/health-sle.png)

Most dashboards hand you a single health percentage and no way to argue with it.
UnifiOptimizer's health number is built so that the score and its explanation are the
same thing. The model is adapted from Juniper Mist, and figure 3 is the whole
idea on one page.

Time runs left to right in five-minute buckets. Each row is a client. For every
bucket a client is actually using the network, that client-minute is judged pass
or fail against a set of service-level expectations: coverage, roaming,
capacity, connection setup, WAN, and infrastructure. A green check is a minute
that met expectations. A red cross is a minute that did not.

The part that keeps the number honest is what happens to a failed minute. It is
pinned to exactly one cause and, where the evidence allows, exactly one device.
The failed minute in the figure is charged to `weak_signal` on `AP-2`, and to
nothing else. No minute is counted twice, so the totals actually add up, and
"where is the pain coming from" has a real answer instead of a vibe.

Look at the bottom row. That client has bad signal the whole hour, and it
contributes zero failed minutes, because it was idle. This is deliberate. Health
is weighted by real impact, so a laptop sitting in a drawer with two bars cannot
drag the score down and cannot hide a genuine problem behind a cosmetic one.

Now the score is easy. Add up the failed minutes. Group them by cause and by
device and you get the breakdown: overall health, each expectation's
sub-score, the count of failed client-minutes, and the single worst offender.
The headline and the explanation come out of one grouping of the same rows,
which is why clicking the number always lands on the reason for it.

## Where to go next

- [`ARCHITECTURE.md`](ARCHITECTURE.md) is the full design: the data model, the
  detector catalog with thresholds, the issue engine's exact transitions, the
  SLE classifiers, and the fix engine.
- The figures are generated by [`tools/diagrams/gen.py`](../tools/diagrams/gen.py);
  its [README](../tools/diagrams/README.md) explains how to regenerate them.
