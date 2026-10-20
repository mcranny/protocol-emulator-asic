`default_nettype none

// The shared bit-serial CRC/LFSR. It mirrors model.Crc one for one.
//
// One message bit is shifted per `shift` strobe, which is how the engine
// drives it: one CRCBIT instruction per bit. It is bit-serial rather than
// byte-wide because low-speed USB needs a 5-bit CRC over an 11-bit field that
// no byte-wide unit handles cleanly.
//
// `msb_first` selects the shift direction, which also selects where the
// polynomial taps sit:
//   - MSB first shifts left and XORs the normal-form polynomial when bit 15
//     shifts out (CRC-16/CCITT, poly 0x1021).
//   - LSB first shifts right and XORs the reflected polynomial when bit 0
//     shifts out (CRC-16/ARC, poly 0xA001, and USB CRC5, poly 0x0014).
//
// Strobes, all sampled on the rising clock edge:
//   configure  latches `poly` and `msb_first`. The register is untouched.
//   load       loads `seed` into the register.
//   shift      shifts `bit_in` through the register using the polynomial and
//              direction latched before this edge.
// `configure` is independent of `load` and `shift`. If `load` and `shift` are
// asserted together, `load` wins. The engine never does either.
//
// Reset matches a freshly constructed model.Crc: polynomial 0, MSB first,
// register 0.
module v3_crc (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        configure,
    input  wire [15:0] poly,
    input  wire        msb_first,
    input  wire        load,
    input  wire [15:0] seed,
    input  wire        shift,
    input  wire        bit_in,
    output wire [15:0] value
);

    reg [15:0] poly_r;
    reg        msb_first_r;
    reg [15:0] state;

    assign value = state;

    // The bit that feeds back into the polynomial. MSB first it is the bit
    // shifting out of the top XOR the message bit; LSB first, the bit shifting
    // out of the bottom XOR the message bit.
    wire feedback = msb_first_r ? (state[15] ^ bit_in) : (state[0] ^ bit_in);

    wire [15:0] shifted = msb_first_r ? {state[14:0], 1'b0} : {1'b0, state[15:1]};

    always @(posedge clk) begin
        if (!rst_n) begin
            poly_r      <= 16'd0;
            msb_first_r <= 1'b1;
            state       <= 16'd0;
        end else begin
            if (configure) begin
                poly_r      <= poly;
                msb_first_r <= msb_first;
            end
            if (load)
                state <= seed;
            else if (shift)
                state <= shifted ^ (feedback ? poly_r : 16'd0);
        end
    end

`ifdef FORMAL
    // Proof bookkeeping only; the first edge has no history to check.
    reg started = 1'b0;
    always @(posedge clk) started <= 1'b1;

    always @(posedge clk) if (started) begin
        if (!$past(rst_n)) begin
            // Reset reproduces a freshly constructed model.Crc.
            assert(state == 16'd0);
            assert(poly_r == 16'd0);
            assert(msb_first_r == 1'b1);
        end else begin
            // Configuration changes only on `configure`, and then takes the
            // new values exactly.
            if ($past(configure)) begin
                assert(poly_r == $past(poly));
                assert(msb_first_r == $past(msb_first));
            end else begin
                assert(poly_r == $past(poly_r));
                assert(msb_first_r == $past(msb_first_r));
            end

            if ($past(load)) begin
                assert(state == $past(seed));
            end else if (!$past(shift)) begin
                // `configure` alone never disturbs the register.
                assert(state == $past(state));
            end else if ($past(msb_first_r)) begin
                // One induction step of the recurrence, MSB first: shift
                // left, then XOR the normal-form polynomial if bit 15 of the
                // previous register XOR the message bit was set.
                if ($past(state[15]) ^ $past(bit_in))
                    assert(state == (($past(state) << 1) ^ $past(poly_r)));
                else
                    assert(state == ($past(state) << 1));
            end else begin
                // The same step, LSB first: shift right, then XOR the
                // reflected polynomial if bit 0 of the previous register XOR
                // the message bit was set.
                if ($past(state[0]) ^ $past(bit_in))
                    assert(state == (($past(state) >> 1) ^ $past(poly_r)));
                else
                    assert(state == ($past(state) >> 1));
            end
        end
    end
`endif

endmodule

`default_nettype wire
