// tb_blink.sv — self-checking SystemVerilog testbench for blink.v
//
// Drives the clock, applies an active-low reset, then free-runs the counter
// and checks that `led` (the MSB of the counter) toggles at the expected rate:
// it stays low for 2**(WIDTH-1) edges, then high for 2**(WIDTH-1), and so on.
//
// We instantiate the DUT with a small WIDTH so the LED flips quickly in
// simulation. Built with Verilator's `--binary` mode, which supplies main().

module tb_blink;

    // Small counter so the LED toggles quickly in simulation.
    localparam int WIDTH = 4;
    localparam int HALF  = 1 << (WIDTH - 1); // edges per LED half-period

    logic clk;
    logic rst_n;
    logic led;

    // DUT with the testbench's narrow counter width.
    blink #(.WIDTH(WIDTH)) dut (
        .clk   (clk),
        .rst_n (rst_n),
        .led   (led)
    );

    // Free-running clock: 10ns period.
    initial clk = 1'b0;
    always #5 clk = ~clk;

    int errors  = 0;
    int toggles = 0;

    initial begin
        // Reference model of the DUT's counter, kept in lockstep so we can
        // predict `led` (== model[WIDTH-1]) without peeking inside the DUT.
        logic [WIDTH-1:0] model;
        logic             prev_led;
        logic             expected;

        // --- Reset: hold rst_n low across a couple of edges ---
        rst_n = 1'b0;
        model = '0;
        repeat (2) @(posedge clk);
        #1;
        if (led !== model[WIDTH-1]) begin
            $display("FAIL: led should be 0 during reset, got %b", led);
            errors++;
        end
        prev_led = led;

        // --- Release reset and free-run for 3 full blink periods ---
        rst_n = 1'b1;

        for (int i = 0; i < HALF * 2 * 3; i++) begin
            @(posedge clk);          // DUT counter increments on this edge
            model = model + 1'b1;    // mirror it
            #1;                      // settle, then sample
            expected = model[WIDTH-1];
            if (led !== expected) begin
                $display("FAIL: edge %0d: led=%b expected=%b (count=%0d)",
                         i, led, expected, model);
                errors++;
            end
            if (led !== prev_led) begin
                toggles++;
                prev_led = led;
            end
        end

        if (toggles < 4) begin
            $display("FAIL: led toggled only %0d times; expected it to blink",
                     toggles);
            errors++;
        end

        if (errors != 0) begin
            $display("TESTBENCH FAILED with %0d error(s)", errors);
            $fatal(1, "testbench failed");
        end

        $display("TESTBENCH PASSED: led blinked %0d times (WIDTH=%0d)",
                 toggles, WIDTH);
        $finish;
    end

endmodule
