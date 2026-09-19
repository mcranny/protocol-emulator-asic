// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module protocol_engine (
    input wire clk, rst_n, ena,
    input wire [7:0] pins_in,
    input wire cmd_valid, frame_error,
    input wire [7:0] cmd, addr,
    input wire [23:0] payload,
    output reg [39:0] reply,
    output wire [7:0] pins_out, pins_oe,
    output reg running,
    output wire error
);
    localparam [10:0] E_ILLEGAL=11'h010, E_ADDRESS=11'h020,
        E_BUSY=11'h040, E_COMMAND=11'h080, E_FRAME=11'h100,
        E_OVERFLOW=11'h200, E_TIMEOUT=11'h400;

    reg [23:0] program_mem [0:63];
    reg [63:0] valid_words;
    reg [5:0] pc;
    reg [15:0] r0, r1, osr, delay_left, wait_left;
    reg [7:0] out_value, out_enable;
    reg [7:0] fifo [0:3];
    reg [1:0] rd_ptr, wr_ptr;
    reg [2:0] level;
    reg [10:0] errors;

    wire [23:0] instruction = program_mem[pc];
    wire [3:0] opcode = instruction[23:20];
    wire [15:0] selected = instruction[16] ? r1 : r0;
    wire [23:0] status = {5'b0, level, 5'b0, errors[10:4],
                         (level == 4), (level == 0), !running, running};
    assign pins_out = out_value;
    assign pins_oe = out_enable & {8{rst_n && ena && running}};
    assign error = |errors;

    function legal;
        input [23:0] word;
        begin
            case (word[23:20])
                0,12: legal = word[19:0] == 0;
                1,2: legal = word[19:16] == 0;
                3: legal = word[19:17] == 0;
                4,5: legal = (word[19:0] & 20'heffff) == 0;
                6: legal = word[19:4] == 0;
                7: legal = (word[19:0] & 20'hefF00) == 0;
                8: legal = word[19:16] == 0 && word[15:0] != 0;
                9: legal = word[19:4] != 0;
                10: legal = (word[19:0] & 20'heffc0) == 0;
                11: legal = word[19:6] == 0;
                default: legal = 0;
            endcase
        end
    endfunction

    reg [10:0] host_error;
    reg [23:0] result;
    always @* begin
        host_error = 0;
        result = 0;
        if (cmd > 9 || ((cmd != 1 && cmd != 2) && addr != 0) ||
            ((cmd != 1 && cmd != 7) && payload != 0) ||
            (cmd == 7 && payload[23:8] != 0))
            host_error = E_COMMAND;
        else if ((cmd == 1 || cmd == 2) && addr >= 64)
            host_error = E_ADDRESS;
        else case (cmd)
            1: begin
                if (running) host_error = E_BUSY;
                else if (!legal(payload)) host_error = E_ILLEGAL;
                else result = payload;
            end
            2: begin
                if (!valid_words[addr[5:0]]) host_error = E_ILLEGAL;
                else result = program_mem[addr[5:0]];
            end
            3: begin
                if (running) host_error = E_BUSY;
                else if (!valid_words[0]) host_error = E_ILLEGAL;
            end
            6: result = status;
            7: begin
                if (level == 4) host_error = E_OVERFLOW;
                else result = {21'b0, level} + 24'd1;
            end
            8: result = {21'b0, level};
            9: result = 24'h010140;
            default: result = 0;
        endcase
        reply = {cmd | {(|host_error), 7'b0}, addr, result};
    end

    wire accepted = cmd_valid && host_error == 0 && !frame_error;
    wire start_cmd = accepted && cmd == 3;
    wire stop_cmd = accepted && cmd == 4;
    wire reset_cmd = accepted && cmd == 5;
    wire fatal_host = frame_error || (cmd_valid && |host_error &&
                     host_error != E_BUSY && host_error != E_OVERFLOW);
    wire execute = running && !fatal_host && !start_cmd && !stop_cmd && !reset_cmd;
    wire legal_fetch = valid_words[pc] && legal(instruction);
    wire match_pin = pins_in[instruction[2:0]] == instruction[3];
    wire wait_expired = !match_pin &&
        ((wait_left == 0 && instruction[19:4] == 1) || wait_left == 1);
    wire branch = opcode == 11 || (opcode == 10 && selected != 1);
    wire retire = execute && legal_fetch && opcode != 12 &&
        (opcode != 4 || level != 0) &&
        (opcode != 8 || delay_left == 1 || (delay_left == 0 && instruction[15:0] == 1)) &&
        (opcode != 9 || match_pin);
    wire [10:0] exec_error = !execute ? 11'b0 :
        !legal_fetch ? E_ILLEGAL :
        (opcode == 9 && wait_expired) ? E_TIMEOUT :
        (retire && !branch && pc == 63) ? E_ADDRESS : 11'b0;
    wire push = accepted && cmd == 7;
    wire pop = execute && legal_fetch && opcode == 4 && level != 0;
    wire write_program = rst_n && ena && accepted && cmd == 1;

    // Storage data need not be reset. Only validity and FIFO count are reset.
    always @(posedge clk) begin
        if (write_program) program_mem[addr[5:0]] <= payload;
        if (rst_n && ena && push) fifo[wr_ptr] <= payload[7:0];
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_words <= 0;
            running <= 0;
            errors <= 0;
            pc <= 0;
            r0 <= 0; r1 <= 0; osr <= 0;
            out_value <= 0; out_enable <= 0;
            delay_left <= 0; wait_left <= 0;
            level <= 0; rd_ptr <= 0; wr_ptr <= 0;
        end else if (!ena || reset_cmd) begin
            running <= 0;
            pc <= 0;
            r0 <= 0; r1 <= 0; osr <= 0;
            out_value <= 0; out_enable <= 0;
            delay_left <= 0; wait_left <= 0;
            level <= 0; rd_ptr <= 0; wr_ptr <= 0;
            if (ena && reset_cmd) errors <= 0;
        end else begin
            errors <= errors | (cmd_valid ? host_error : 11'b0) |
                      (frame_error ? E_FRAME : 11'b0) | exec_error;
            if (write_program) valid_words[addr[5:0]] <= 1;
            if (push) wr_ptr <= wr_ptr + 1'b1;
            if (pop) rd_ptr <= rd_ptr + 1'b1;
            case ({push,pop})
                2'b10: level <= level + 1'b1;
                2'b01: level <= level - 1'b1;
                default: level <= level;
            endcase
            if (start_cmd) begin
                running <= 1;
                pc <= 0;
                r0 <= 0; r1 <= 0; osr <= 0;
                out_value <= 0; out_enable <= 0;
                delay_left <= 0; wait_left <= 0;
            end else if (stop_cmd || fatal_host) running <= 0;
            else if (execute) begin
                if (|exec_error) running <= 0;
                if (legal_fetch) begin
                    if (retire && (pc != 63 || branch))
                        pc <= branch ? instruction[5:0] : pc + 1'b1;
                    case (opcode)
                        1: out_value <= (out_value & ~instruction[15:8]) |
                                        (instruction[7:0] & instruction[15:8]);
                        2: out_enable <= (out_enable & ~instruction[15:8]) |
                                         (instruction[7:0] & instruction[15:8]);
                        3: if (instruction[16]) r1 <= instruction[15:0];
                           else r0 <= instruction[15:0];
                        4: if (pop) begin
                            if (instruction[16]) r1 <= {8'b0, fifo[rd_ptr]};
                            else r0 <= {8'b0, fifo[rd_ptr]};
                        end
                        5: osr <= selected;
                        6: begin
                            out_value[instruction[2:0]] <= instruction[3] ? osr[15] : osr[0];
                            osr <= instruction[3] ? {osr[14:0],1'b0} : {1'b0,osr[15:1]};
                        end
                        7: if (instruction[16]) r1 <= {8'b0, pins_in & instruction[7:0]};
                           else r0 <= {8'b0, pins_in & instruction[7:0]};
                        8: if (delay_left != 0) delay_left <= delay_left - 1'b1;
                           else delay_left <= instruction[15:0] - 1'b1;
                        9: if (match_pin || wait_expired) wait_left <= 0;
                           else wait_left <= (wait_left != 0 ? wait_left : instruction[19:4]) - 1'b1;
                        10: if (instruction[16]) r1 <= r1 - 1'b1;
                            else r0 <= r0 - 1'b1;
                        12: running <= 0;
                        default: begin end
                    endcase
                end
            end
        end
    end

`ifdef FORMAL
    (* anyconst *) reg [5:0] watched_word;
    reg past_valid = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(!rst_n);
        if (past_valid && rst_n) begin
            assert(level <= 4);
            assert(pc < 64);
            if (!ena || !running) assert(pins_oe == 0);
            if ($past(rst_n && ena && running && cmd_valid && cmd == 1 && !frame_error))
                begin
                    assert(valid_words == $past(valid_words));
                    assert(program_mem[watched_word] == $past(program_mem[watched_word]));
                end
            if ($past(rst_n && ena && (fatal_host || |exec_error)))
                assert(!running);
            if ($past(rst_n && ena && execute && !legal_fetch))
                assert(errors[4] && !running);
        end
        if (!rst_n) assert(pins_oe == 0);
    end
`endif
endmodule
