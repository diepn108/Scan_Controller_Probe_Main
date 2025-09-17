from scanner.probe_controller import ProbePlugin
from scanner.plugin_setting import PluginSettingString, PluginSettingInteger, PluginSettingFloat
from scanner.MS461xxVISA_Implementation import InstrumentConnection
import scanner.Plugins.VNA_List_Sparams as VNA_List_Sparams
import re
import numpy as np

### This plugin supports the Anritsu 37397C and 37347C 2-port VNAs

class VNA_Plugin(ProbePlugin):
    def __init__(self):
        super().__init__()
        self.vna = None
        self.frequency_data_query = []
        
        # VNA model selection
        self.vna_model = PluginSettingString(
            "VNA Model", "37397C",
            select_options=["37397C", "37347C"], restrict_selections=True
        )
        self.address = PluginSettingString("Resource Address", "GPIB0::6::INSTR") #GPIB1 for Anritsu 37347C, GPIB0 for Anritsu 37397C
        self.timeout = PluginSettingInteger("Timeout (ms)", 20000)

        self.freq_mode = PluginSettingString(
            "Frequency Mode Query or Write", "Query",
            select_options=["Query", "Write"], restrict_selections=True
        )

        self.freq_start = PluginSettingFloat("Start Freq (Hz)", 1e9 if self.vna_model.value == "37397C" else 40e6)
        self.freq_stop = PluginSettingFloat("Stop Freq (Hz)", 4e9 if self.vna_model.value == "37397C" else 20e9)

        self.if_bandwidth = PluginSettingFloat("IF Bandwidth (Hz)", 1000)   
        
        self.error_correction = PluginSettingString(
            "Error Correction Mode", "Query",
            select_options=["Query", "On", "Off"], restrict_selections=True
        )


        self.data_mode = PluginSettingString(
            "Data Mode (Raw or Corrected)", "Corrected",
            select_options=["Raw", "Corrected"], restrict_selections=True
        )

        self.add_setting_pre_connect(self.vna_model)
        self.add_setting_pre_connect(self.address)
        self.add_setting_pre_connect(self.timeout)
        self.add_setting_pre_connect(self.freq_mode)
        self.add_setting_pre_connect(self.freq_start)
        self.add_setting_pre_connect(self.freq_stop)
        self.add_setting_pre_connect(self.if_bandwidth)
        self.add_setting_pre_connect(self.error_correction)
        self.add_setting_pre_connect(self.data_mode)

    def connect(self):
        try:
            self.vna = InstrumentConnection(self.address.value, self.timeout.value).connect()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize VISA connection, message error is : {str(e)}")
        
        if self.vna is None:
            raise RuntimeError("VNA connection failed; no instrument object created.")
        
        # Set to ASCII data format for easy parsing
        self.vna.write("FMA")
        
        start_Frequency = None
        stop_Frequency = None
        if self.freq_mode.value == "Query":
            raw_start = self.vna.query("SRT?")
            if not raw_start or not raw_start.strip():
                raise ValueError("Empty or invalid response from SRT? query")
            start_Frequency = float(raw_start.strip())
            
            raw_stop = self.vna.query("STP?")
            if not raw_stop or not raw_stop.strip():
                raise ValueError("Empty or invalid response from STP? query")
            stop_Frequency = float(raw_stop.strip())
            
            raw_ifbw = self.vna.query("IFX?")
            if not raw_ifbw or not raw_ifbw.strip():
                raise ValueError("Empty or invalid response from IFX? query")
            ifbw_code = int(raw_ifbw.strip())
            ifbw_map = {1:10, 2:100, 3:1000, 4:10000, 5:30000}
            intermediate_Frequency = ifbw_map.get(ifbw_code, 1000)

            PluginSettingFloat.set_value_from_string(self.freq_start, f"{start_Frequency}")
            PluginSettingFloat.set_value_from_string(self.freq_stop, f"{stop_Frequency}")
            PluginSettingFloat.set_value_from_string(self.if_bandwidth, f"{intermediate_Frequency}")

        elif self.freq_mode.value == "Write":
            # Validate frequency range based on model
            if self.vna_model.value == "37397C":
                if self.freq_start.value < 40e6:
                    raise ValueError("Start frequency must be >= 40 MHz for 37397C")
                if self.freq_stop.value > 65e9:
                    raise ValueError("Stop frequency must be <= 65 GHz for 37397C")
            elif self.vna_model.value == "37347C":
                if self.freq_start.value < 40e6:
                    raise ValueError("Start frequency must be >= 40 MHz for 37347C")
                if self.freq_stop.value > 20e9:
                    raise ValueError("Stop frequency must be <= 20 GHz for 37347C")

            self.vna.write(f"SRT {self.freq_start.value} HZ")
            self.vna.write(f"STP {self.freq_stop.value} HZ")
            start_Frequency = self.freq_start.value
            stop_Frequency = self.freq_stop.value
            ifbw = self.if_bandwidth.value
            if ifbw <= 10:
                self.vna.write("IF1")
            elif ifbw <= 100:
                self.vna.write("IF2")
            elif ifbw <= 1000:
                self.vna.write("IF3")
            elif ifbw <= 10000:
                self.vna.write("IF4")
            else:
                self.vna.write("IFA")

        else:
            raise ValueError("__Incorrect Input on frequency mode setting__")

        # For 2 ports
        self.selected_params = VNA_List_Sparams.s_parameter_selection_VNA_n_channels(2)

        ec_mode = self.error_correction.value
        if ec_mode == "Query":
            ec_status = self.vna.query("CON?")
            if ec_status != "1":
                print("Warning: Error correction is off during Query mode.")
            cal_type = self.vna.query("CXX?")
            if start_Frequency is not None and stop_Frequency is not None:
                cal_start = float(self.vna.query("CSF?"))
                cal_stop = float(self.vna.query("CTF?"))
                if cal_start != start_Frequency or cal_stop != stop_Frequency:
                    print("Warning: Calibration frequency range does not match current settings.")
        elif ec_mode == "On":
            self.vna.write("CON")
        elif ec_mode == "Off":
            self.vna.write("COF")

        self.vna.write("HLD")
        opc_done = self.vna.query("*OPC?")
        if not (opc_done == "1"):
            raise RuntimeError(f"Error, Opc returned unexpected value while waiting for a single sweep to finish (expected '1', received {opc_done}); ending code execution.")
            self.vna.close()

        raw_freq = self.vna.query("OFV")
        self.frequency_data_query = self._strip_block(raw_freq)

    def disconnect(self):
        if self.vna:
            self.vna.close()

    def get_xaxis_coords(self):
        raw = self.vna.query("OFV")
        return tuple(map(float, self._strip_block(raw)))

    def get_xaxis_units(self):
        return "Hz"

    def get_yaxis_units(self):
        pass

    def get_channel_names(self):
        return self.selected_params 

    def scan_begin(self):
        self.vna.write("SNG")
        self.vna.query("*OPC?")

    def scan_trigger_and_wait(self, scan_index=None, scan_location=None):
        self.scan_read_measurement_hdf5()

    def scan_end(self):
        pass

    def _strip_block(self, raw):
        if raw.startswith("#"):
            hdr_digits = int(raw[1])           
            hdr_len = int(raw[2:2 + hdr_digits])
            raw = raw[2 + hdr_digits:2 + hdr_digits + hdr_len]         
        parts = re.split(r"[,\s]+", raw.strip())
        return parts

    def scan_read_measurement(self, scan_index=None, scan_location=None):
        results = {}
        suffix = "R" if self.data_mode.value == "Raw" else "C"
        if self.data_mode.value == "Corrected":
            ec_status = self.vna.query("CON?")
            if ec_status != "1":
                print("Warning: Using corrected data but error correction is off; results may be inaccurate.")
        param_map = {"S11": f"OS11{suffix}", "S21": f"OS21{suffix}", "S12": f"OS12{suffix}", "S22": f"OS22{suffix}"}
        for name in self.get_channel_names():
            cmd = param_map.get(name)
            if cmd:
                raw = self.vna.query(cmd)
                tokens = self._strip_block(raw)
                vals = list(map(float, tokens))
                results[name] = np.array([complex(vals[i], vals[i+1])
                                for i in range(0, len(vals), 2)])
        return results
