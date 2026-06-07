// blink.v — a minimal LED blinker
//
// A free-running counter divides the input clock; the most-significant counter
// bit drives the LED, so it toggles at roughly clk / 2**WIDTH. This is the
// classic "hello world" design for an FPGA synthesis + place-and-route flow.
//
// Plain Verilog-2001 so it elaborates cleanly in yosys (`read_verilog`).

module blink #(
    parameter WIDTH = 24          // counter width — sets the blink rate
) (
    input  wire clk,              // system clock
    input  wire rst_n,            // active-low asynchronous reset
    output wire led               // LED output
);

    reg [WIDTH-1:0] counter;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            counter <= {WIDTH{1'b0}};
        else
            counter <= counter + 1'b1;
    end

    assign led = counter[WIDTH-1];

`ifdef FORMAL
    // ---- Formal properties (see blink_formal.sby) ----------------------
    // Guarded by FORMAL so they never reach synthesis/place-and-route.

    // Track whether at least one clock edge has elapsed, so $past() is valid.
    reg f_past_valid = 1'b0;
    always @(posedge clk)
        f_past_valid <= 1'b1;

    always @(posedge clk) begin
        // The LED is, by construction, the MSB of the counter.
        assert (led == counter[WIDTH-1]);

        // Whenever we were out of reset last cycle and remain out of reset,
        // the counter advances by exactly one (mod 2**WIDTH).
        if (f_past_valid && $past(rst_n) && rst_n)
            assert (counter == ($past(counter) + 1'b1));
    end

    // Asynchronous reset holds the counter (and therefore the LED) at zero.
    always @(*)
        if (!rst_n) begin
            assert (counter == {WIDTH{1'b0}});
            assert (led == 1'b0);
        end

    // Liveness-as-reachability: the LED can actually turn on. A cover trace
    // proves the blinker is not stuck dark.
    always @(posedge clk)
        cover (f_past_valid && led);
`endif

endmodule
