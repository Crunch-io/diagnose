# Diagnose

A library for instrumenting Python code at runtime.

## Probes

Structured logs and metrics and observability are great, but almost always
require you to alter your code, which interrupts the flow when reading code.
It also typically requires a build cycle to alter; it's no fun making a ticket,
getting review, waiting for a build and deploy, and then doing it all again
to back out your temporary additions. This is doubly true when doing research,
where you might perform a dozen small experiments to measure your live code.

This library allows you to dynamically add probes at runtime instead.
Probes are:
* reliable: errors will never affect your production code
* ephemeral: set a "lifespan" (in minutes) for each instrument
* comprehensive: all references to the target function are instrumented
* fast: measure most functions with fast local lookups; uses hunter (in Cython) for more invasive internal probes.

Individual probes can be created directly by calling `attach_to(target)`:

```#python
>>> from path.to.module import myclass
>>> myclass().add13(arg=5)
18
>>> p = diagnose.probes.attach_to("path.to.module.myclass.add13")
>>> p.instruments["foo"] = diagnose.LogInstrument("foo", "arg")
>>> p.start()
>>> myclass().add13(arg=5)
Probe (foo) = 5
18
```

Instruments aren't limited to recording devices! Use probes to fire off any kind of event handler. Instruments are free to maintain state themselves, or read it from somewhere else, to control their own behavior or even implement feedback mechanisms. A truly evil instrument could even alter the args/kwargs passed to a function on the fly, or call arbitrary Python code to do any number of crazy things. Consequently, it's up to you to govern what instruments are added to your environment.

## Managers

In a running system, we want to add, remove, start, and stop probes and instruments without having to code at an interactive prompt or restart the system; we do this with an InstrumentManager. Start by configuring the global diagnose.manager:

```#python
>>> diagnose.manager.instrument_classes = {
    "log": LogInstrument,
    "hist": MyHistogramInstrument,
    "incr": MyIncrementInstrument,
}
>>> diagnose.manager.global_namespace.update({"foo": foo})
```

Later, you can define instruments:

```#python
>>> diagnose.manager.specs["instr-1"] = {
    "target": "myapp.module.file.class.method",
    "instrument": {
        "type": "log",
        "name": "myapp.method",
        "value": "result",
        "event": "return",
        "custom": {},
    },
    "lifespan": 10,
    "lastmodified": datetime.datetime.utcnow(),
    "applied": {},
}
```

Then call `diagnose.manager.apply()`, either when you add an instrument, or on a schedule if your store is in MongoDB and the process defining probes is not the target process.

The `applied` dictionary will be filled with information about which processes
have applied the probe, and whether they encountered any errors.

## Breakpoints

Breakpoints allow you to perform tests that involve concurrency, or that must trigger specific actions at specific times, by setting specific
breakpoints at which the execution must stop waiting for some conditions to happen.

```#python
        with Breakpoint(S3Archive, "unarchive") as bp:
            bp.start_thread(object.unarchive)  # Start something in the background that will invoke S3Archive.unarchive
            bp.wait()  # wait for S3Archive.unarchive to start

            # perform what has to be done once S3Archive.unarchive
            # has been started.
            # Note: you can get at the unarchive's stack frame using
            # bp.stackframe

            bp.release()  # let S3Archive.unarchive proceed.
```
