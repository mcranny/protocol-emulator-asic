`default_nettype none

// A ring-buffer queue sized for the engine's TX byte queue and RX record
// queue. Storage is DEPTH entries of WIDTH bits, addressed by independent read
// and write pointers with a separate level counter so wraparound is explicit.
//
// A push presents up to PUSH_MAX entries at once in `push_data`, the first
// entry in the most significant WIDTH-bit slice, with `push_count` naming how
// many are valid. The whole group is atomic: it is accepted only when the
// count is non-zero and the entries all fit against the level at the start of
// the cycle, and otherwise nothing is written. `push_ok` is that acceptance
// term, combinational, so the host can reject a TXPUSH in the same frame.
//
// A pop removes the oldest entry when the queue is non-empty. `head` is that
// entry, combinational straight from storage, so a host reply or the engine
// sees it in the same cycle it pops. A push and a pop in the same cycle both
// apply.
//
// Reset clears the pointers and the level. START and STOP do not reach here:
// the queues keep their contents across a run, matching the engine's model.
module v3_fifo #(
    parameter WIDTH    = 8,
    parameter DEPTH    = 16,
    parameter PUSH_MAX = 1
) (
    input  wire                           clk,
    input  wire                           rst_n,
    input  wire [$clog2(PUSH_MAX+1)-1:0]  push_count,
    input  wire [PUSH_MAX*WIDTH-1:0]      push_data,
    output wire                           push_ok,
    input  wire                           pop,
    output wire [WIDTH-1:0]               head,
    output wire [$clog2(DEPTH):0]         level
);

    // DEPTH must be a power of two so the pointers wrap naturally.
    localparam PTR_W  = $clog2(DEPTH);
    localparam LVL_W  = $clog2(DEPTH) + 1;
    localparam PUSH_W = $clog2(PUSH_MAX + 1);

    reg [WIDTH-1:0] storage [0:DEPTH-1];
    reg [PTR_W-1:0] rd_ptr, wr_ptr;
    reg [LVL_W-1:0] level_r;

    // The oldest entry, straight from storage.
    assign head = storage[rd_ptr];
    assign level = level_r;

    // The push count, widened to the level and pointer arithmetic.
    wire [LVL_W-1:0] push_level = {{(LVL_W-PUSH_W){1'b0}}, push_count};
    wire [PTR_W-1:0] push_step  = {{(PTR_W-PUSH_W){1'b0}}, push_count};

    // Atomic fit test against the level at the start of the cycle.
    assign push_ok = (push_count != {PUSH_W{1'b0}}) &&
                     ((level_r + push_level) <= DEPTH[LVL_W-1:0]);

    // A pop is honoured only when there is an entry to remove.
    wire do_pop = pop && (level_r != {LVL_W{1'b0}});

    wire [LVL_W-1:0] push_amt = push_ok ? push_level : {LVL_W{1'b0}};
    wire [LVL_W-1:0] pop_amt  = do_pop  ? {{(LVL_W-1){1'b0}}, 1'b1} : {LVL_W{1'b0}};

    integer k;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_ptr  <= {PTR_W{1'b0}};
            wr_ptr  <= {PTR_W{1'b0}};
            level_r <= {LVL_W{1'b0}};
        end else begin
            if (push_ok) begin
                for (k = 0; k < PUSH_MAX; k = k + 1) begin
                    if (k < push_count)
                        storage[wr_ptr + k[PTR_W-1:0]] <=
                            push_data[(PUSH_MAX-1-k)*WIDTH +: WIDTH];
                end
                wr_ptr <= wr_ptr + push_step;
            end
            if (do_pop)
                rd_ptr <= rd_ptr + {{(PTR_W-1){1'b0}}, 1'b1};
            level_r <= level_r + push_amt - pop_amt;
        end
    end

`ifdef FORMAL
    // Proof bookkeeping only; the first edge has no history to check and is
    // constrained to be a reset so the state-invariant asserts start from a
    // reachable state.
    reg started = 1'b0;
    always @(posedge clk) begin
        started <= 1'b1;
        if (!started) assume(!rst_n);
    end

    // The first presented entry sits in the most significant slice.
    wire [WIDTH-1:0] first_in = push_data[(PUSH_MAX-1)*WIDTH +: WIDTH];

    always @(posedge clk) if (started) begin
        if (!rst_n && !$past(rst_n)) begin
            // Reset state.
            assert(level_r == 0);
            assert(rd_ptr == 0);
            assert(wr_ptr == 0);
        end else if (rst_n && $past(rst_n)) begin
            // The level tracks the entries exactly: it rises by an accepted
            // push and falls by an accepted pop.
            assert(level_r == $past(level_r)
                   + ($past(push_ok) ? $past(push_count) : 0)
                   - (($past(pop) && $past(level_r) != 0) ? 1 : 0));
            // The write pointer only moves for an accepted push, and then by
            // exactly the accepted count; a rejected push writes nothing.
            if ($past(push_ok))
                assert(wr_ptr == ($past(wr_ptr) + $past(push_count)) % DEPTH);
            else
                assert(wr_ptr == $past(wr_ptr));
            // The read pointer advances by one only on an accepted pop.
            if ($past(pop) && $past(level_r) != 0)
                assert(rd_ptr == ($past(rd_ptr) + 1) % DEPTH);
            else
                assert(rd_ptr == $past(rd_ptr));
            // An accepted push lands its first entry at the old write pointer.
            if ($past(push_ok))
                assert(storage[$past(wr_ptr)] == $past(first_in));
        end
    end

    // Invariants that must hold on every cycle out of reset.
    always @* if (started && rst_n) begin
        // Occupancy never exceeds the depth.
        assert(level_r <= DEPTH);
        // Acceptance is exactly the atomic-fit term.
        assert(push_ok == ((push_count != 0) && ((level_r + push_count) <= DEPTH)));
        // The head is the oldest entry.
        assert(head == storage[rd_ptr]);
    end
`endif

endmodule

`default_nettype wire
