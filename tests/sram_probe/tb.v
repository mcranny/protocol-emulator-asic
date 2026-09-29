`timescale 1ns/1ps
module tb;
    reg clk=0; always #20 clk=~clk;
    reg rst_n=0, ena=1;
    reg [7:0] ui=0, uio=0;
    wire [7:0] out, unused_out, oe;
    reg [63:0] expected[0:63];
    integer address, byte_index;
    tt_um_mcranny_sram_probe dut(ui,out,uio,unused_out,oe,ena,clk,rst_n);
    task tick;
        begin @(posedge clk); #10; @(negedge clk); end
    endtask
    initial begin
        tick; rst_n=1;
        for (address=0; address<64; address=address+1) begin
            expected[address]=64'h0123456789abcdef ^ (64'h0102040810204080 * address);
            for (byte_index=7;byte_index>=0;byte_index=byte_index-1) begin
                ui=8'h40;uio=expected[address] >> (byte_index*8);tick;
            end
            ui=8'h80 | address;tick;
        end
        // Disabled writes must preserve contents.
        ena=0;ui=8'h80;tick;ena=1;
        for (address=63;address>=0;address=address-1) begin
            ui=address;tick;
            for (byte_index=0;byte_index<8;byte_index=byte_index+1) begin
                uio=byte_index;#1;
                if (out !== ((expected[address] >> (byte_index*8)) & 8'hff))
                    $fatal(1,"Mismatch address=%0d byte=%0d got=%h",address,byte_index,out);
                if (oe !== 0 || unused_out !== 0) $fatal(1,"unexpected IO drive");
            end
        end
        $display("PASS: 64 addresses, all 512 bytes, disabled write, IO enables");
        $finish;
    end
endmodule
