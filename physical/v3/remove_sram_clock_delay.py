# Clock-tree synthesis inserts delaybuf_<n>_clk, a chain in front of both SRAM
# clocks. This removes that chain and reconnects its loads to the net that
# drove it, so hold repair sees the earlier arrival. Invoked by OpenROAD as an
# OdbpyStep: the input database is the positional argument and the outputs are
# --output-odb and --output-def.


def remove_sram_clock_delay(block, destroy_inst, destroy_net):
    """Return how many delay buffers were removed.

    ``destroy_inst`` and ``destroy_net`` are the OpenDB destroy calls. The
    chain must be contiguous from ``delaybuf_0_clk`` and its last buffer must
    drive the clock subtree, not another delay buffer and not a port.
    """
    buffers = []
    while True:
        inst = block.findInst(f"delaybuf_{len(buffers)}_clk")
        if inst is None:
            break
        buffers.append(inst)
    if not buffers:
        raise RuntimeError("CTS inserted no delaybuf_*_clk cells")

    def pin(inst, name):
        term = inst.findITerm(name)
        if term is None or term.getNet() is None:
            raise RuntimeError(f"{inst.getName()}/{name} is not connected")
        return term

    def sinks(net, driver):
        return [it for it in net.getITerms() if it.getInst().getName() != driver.getName()]

    source = pin(buffers[0], "A").getNet()
    for index in range(len(buffers) - 1):
        net = pin(buffers[index], "X").getNet()
        driven = sinks(net, buffers[index])
        nxt = buffers[index + 1].getName()
        if len(driven) != 1 or driven[0].getInst().getName() != nxt or list(net.getBTerms()):
            raise RuntimeError(f"{buffers[index].getName()} does not drive only {nxt}")
    output = pin(buffers[-1], "X").getNet()
    loads = sinks(output, buffers[-1])
    if not loads or list(output.getBTerms()):
        raise RuntimeError(f"{buffers[-1].getName()} does not drive the clock subtree")
    if any(it.getInst().getName().startswith("delaybuf_") for it in loads):
        raise RuntimeError("the delaybuf_*_clk chain is not contiguous")

    nets = [pin(buf, "X").getNet() for buf in buffers]
    for term in loads:
        term.disconnect()
        term.connect(source)
    for buf in buffers:
        destroy_inst(buf)
    for net in nets:
        destroy_net(net)
    return len(buffers)


def main():
    import odb
    from reader import click, click_odb

    @click.command()
    @click_odb
    def command(reader):
        count = remove_sram_clock_delay(
            reader.chip.getBlock(), odb.dbInst.destroy, odb.dbNet.destroy)
        print(f"removed {count} SRAM clock delay buffers")

    command()


if __name__ == "__main__":
    main()
