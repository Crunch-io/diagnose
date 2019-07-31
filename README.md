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

## Instruments

Each probe can have multiple instruments. When a probe is hit by the runtime, it fires off the event to each instrument. Instruments have the following properties:
* name: a name for the instrument; may be used in output, such as when constructing statsd metric names.
* value: a Python expression to be evaluated; its result is used as the instrument sees fit: logged, sent as a metric value, or other output. For statsd instruments, return a list of numbers to emit multiple data points, or a list of (number, tag/tags) pairs to facet them by tags.
* event: a string declaring when to fire the instrument, one of:
    * call: evaluate the value just before calling the probed function, in a context which contains the local variables:
        * start: float time.time()
        * now: datetime.datetime.utcnow()
        * args/kwargs: inputs to the target function; these are also included in locals() by their argnames. For example, `def foo(self, bar, **kwargs)` will place "self" and "bar" in this local namespace, plus the names of any other kwargs passed to the function.
        * frame: `sys._getframe()` of the patch wrapper. The name of the function that called the target, for example, is `frame.f_back.f_code.co_name`.
     * return: the default; evaluate the value just after the probed function returns, in a context with the additional locals:
        * result: the return value of the target function
        * end/elapsed: float time.time()s
        * hotspots: if referenced in the value or custom["tags"], settrace() is used to time each line of code in the probed function (and no deeper, but still VERY slow--USE WITH CARE). This object has the following attributes:
            * slowest: a namedtuple of (time, lineno, source) fields for the line with the slowest <i>single</i> execution time.
            * worst: a namedtuple of (time, lineno, source) fields for the line with the worst <i>cumulative</i> execution time.
            * calls: a dict of (lineno, [count, max, sum]) pairs.
            * filename: the name of the file in which the target is found.
            * source(lineno): the source code for the given lineno in self.filename. Used to populate slowest/worst.
     * end: evaluate the value in the context of the probed function just before it returns. This requires settrace() which is much more expensive; use sparingly.
* expires: a datetime, after which point the instrument will not fire, or None to mean no expiration
* custom: a dict of any additional data for subclasses. May include other information for filtering events, set points for closed-loop controllers, or other information specific to the kind of instrument. The Instrument base class understands the following members:
    * tags: a Python expression to be evaluated, which must return a dict or list of tags to include.

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
