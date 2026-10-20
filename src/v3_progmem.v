// V3 program memory wrapper: a 256x32 single-port SRAM plus a validity bitmap.
//
// Read contract: the address presented with fetch_en on one clock edge returns
// its word on fetch_data after that edge, so the data is valid for the next
// edge to sample. fetch_data holds while fetch_en is low. A write has the
// port, so a fetch on a cycle with write_en returns nothing new.
//
// Compile modes:
//   V3_SRAM_BLACKBOX  declare the macro as a blackbox for synthesis
//   FUNCTIONAL        instantiate the PDK behavioral model of the macro
//   neither           a register-file model of the same macro, for fast
//                     simulation and formal proofs
//
// Macro tie-offs: A_DLY is 1, every A_BIST_* input is low, and every write
// enables all 32 bits through A_BM.
`default_nettype none

`ifdef V3_SRAM_BLACKBOX
`include "v3_sram_blackbox.vh"
`endif

module v3_progmem (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        fetch_en,       // drives A_MEN; low while the engine stalls
    input  wire [7:0]  fetch_addr,
    output wire [31:0] fetch_data,     // valid the cycle after fetch_addr
    output wire        fetch_valid,    // the fetched word was written
    input  wire        write_en,
    input  wire [7:0]  write_addr,
    input  wire [31:0] write_data,
    input  wire        invalidate,     // clears the bitmap on deselect
    output wire        image_complete  // all 256 bits set; START requires this
);

    // Macro port drive.
    wire        a_men  = fetch_en | write_en;
    wire        a_wen  = write_en;
    wire        a_ren  = fetch_en & ~write_en;
    wire [7:0]  a_addr = write_en ? write_addr : fetch_addr;
    wire        a_dly  = 1'b1;
    wire [31:0] a_bm   = 32'hFFFFFFFF;
    wire        a_bist_clk  = 1'b0;
    wire        a_bist_en   = 1'b0;
    wire        a_bist_men  = 1'b0;
    wire        a_bist_wen  = 1'b0;
    wire        a_bist_ren  = 1'b0;
    wire [7:0]  a_bist_addr = 8'h00;
    wire [31:0] a_bist_din  = 32'h00000000;
    wire [31:0] a_bist_bm   = 32'h00000000;
    wire [31:0] a_dout;

    // a_dly and the A_BIST_* tie-offs only wire to the SRAM macro; the
    // register-file model used by the default (macro-less) build ignores them.
    // Sink them here so a plain -Wall build stays clean.
    wire _unused_pm = &{1'b0, a_dly, a_bist_clk, a_bist_en, a_bist_men,
                        a_bist_wen, a_bist_ren, a_bist_addr, a_bist_din,
                        a_bist_bm};

`ifdef V3_SRAM_BLACKBOX
`define V3_PROGMEM_MACRO
`endif
`ifdef FUNCTIONAL
`define V3_PROGMEM_MACRO
`endif

`ifdef V3_PROGMEM_MACRO
    RM_IHPSG13_1P_256x32_c2_bm_bist u_sram (
        .A_CLK      (clk),
        .A_MEN      (a_men),
        .A_WEN      (a_wen),
        .A_REN      (a_ren),
        .A_ADDR     (a_addr),
        .A_DIN      (write_data),
        .A_DLY      (a_dly),
        .A_DOUT     (a_dout),
        .A_BM       (a_bm),
        .A_BIST_CLK (a_bist_clk),
        .A_BIST_EN  (a_bist_en),
        .A_BIST_MEN (a_bist_men),
        .A_BIST_WEN (a_bist_wen),
        .A_BIST_REN (a_bist_ren),
        .A_BIST_ADDR(a_bist_addr),
        .A_BIST_DIN (a_bist_din),
        .A_BIST_BM  (a_bist_bm)
    );
`else
    // Register-file model of the macro: one registered read port shared with
    // the write port, no reset, contents undefined until written.
    reg [31:0] mem [0:255];
    reg [31:0] dout_r;

    always @(posedge clk) begin
        if (a_men && a_wen)
            mem[a_addr] <= (mem[a_addr] & ~a_bm) | (write_data & a_bm);
        else if (a_men && a_ren)
            dout_r <= mem[a_addr];
    end

    assign a_dout = dout_r;
`endif

    assign fetch_data = a_dout;

    // One validity bit per word. Reset and invalidate clear it; a write sets
    // the bit for its address. Invalidate wins over a write on the same edge.
    reg [255:0] bitmap;
    reg         fetch_valid_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bitmap        <= 256'b0;
            fetch_valid_r <= 1'b0;
        end else begin
            if (invalidate)
                bitmap <= 256'b0;
            else if (write_en)
                bitmap <= bitmap | (256'b1 << write_addr);
            if (a_ren)
                fetch_valid_r <= bitmap[fetch_addr];
        end
    end

    assign fetch_valid    = fetch_valid_r;
    assign image_complete = &bitmap;

`ifdef FORMAL
    reg past_valid = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(!rst_n);
    end

    // Macro tie-offs and enable.
    always @* begin
        assert(a_dly == 1'b1);
        assert(a_bist_clk == 1'b0);
        assert(a_bist_en == 1'b0);
        assert(a_bist_men == 1'b0);
        assert(a_bist_wen == 1'b0);
        assert(a_bist_ren == 1'b0);
        assert(a_bist_addr == 8'h00);
        assert(a_bist_din == 32'h00000000);
        assert(a_bist_bm == 32'h00000000);
        assert(a_bm == 32'hFFFFFFFF);
        assert(a_men == (fetch_en | write_en));
        if (!fetch_en && !write_en) assert(!a_men);
        // Reads and writes never overlap on the single port.
        assert(!(a_ren && a_wen));
    end

    // Bitmap properties.
    always @* assert(image_complete == (bitmap == {256{1'b1}}));

    always @(posedge clk) begin
        if (past_valid && rst_n && $past(rst_n)) begin
            // A write sets exactly one bit and leaves the others alone.
            if ($past(write_en) && !$past(invalidate))
                assert(bitmap == ($past(bitmap) | (256'b1 << $past(write_addr))));
            // Invalidate clears every bit.
            if ($past(invalidate))
                assert(bitmap == 256'b0);
            // With neither, the bitmap holds.
            if (!$past(write_en) && !$past(invalidate))
                assert(bitmap == $past(bitmap));
        end
        if (past_valid && !$past(rst_n) && !rst_n)
            assert(bitmap == 256'b0);
    end

`ifndef V3_PROGMEM_MACRO
    // Data presented at N+1 matches the word written to that address. The
    // watched address is an arbitrary constant, so the proof covers every one.
    (* anyconst *) reg [7:0] watched_addr;
    reg [31:0] watched_word;
    reg        watched_written = 0;
    reg        hit_r = 0;
    reg [31:0] hit_word_r;

    always @(posedge clk) begin
        hit_r      <= a_ren && fetch_addr == watched_addr && watched_written;
        hit_word_r <= watched_word;
        if (write_en && write_addr == watched_addr) begin
            watched_written <= 1;
            watched_word    <= write_data;
        end
    end

    // Strengthening for induction: the model holds the watched word.
    always @* if (watched_written) assert(mem[watched_addr] == watched_word);

    always @* if (past_valid && hit_r) assert(fetch_data == hit_word_r);
`endif
`endif

endmodule

`ifdef V3_PROGMEM_MACRO
`undef V3_PROGMEM_MACRO
`endif
