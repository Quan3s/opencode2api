package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"

	utls "github.com/refraction-networking/utls"
)

func bunSpec() *utls.ClientHelloSpec {
	return &utls.ClientHelloSpec{
		CipherSuites: []uint16{
			4865, 4866, 4867, 49195, 49199, 49196, 49200, 52393, 52392,
			49161, 49171, 49162, 49172, 156, 157, 47, 53,
		},
		CompressionMethods: []uint8{0},
		TLSVersMin:         0x0303,
		TLSVersMax:         0x0304,
		Extensions: []utls.TLSExtension{
			&utls.SNIExtension{},
			&utls.ExtendedMasterSecretExtension{},
			&utls.RenegotiationInfoExtension{},
			&utls.SupportedCurvesExtension{Curves: []utls.CurveID{4588, 29, 23, 24}},
			&utls.SupportedPointsExtension{SupportedPoints: []uint8{0}},
			&utls.SessionTicketExtension{},
			&utls.ALPNExtension{AlpnProtocols: []string{"http/1.1"}},
			&utls.StatusRequestExtension{},
			&utls.SignatureAlgorithmsExtension{SupportedSignatureAlgorithms: []utls.SignatureScheme{
				1027, 2052, 1025, 1283, 2053, 1281, 2054, 1537, 2055, 1539, 513,
			}},
			&utls.SCTExtension{},
			&utls.KeyShareExtension{KeyShares: []utls.KeyShare{
				{Group: utls.CurveID(4588)}, {Group: utls.CurveID(29)},
			}},
			&utls.PSKKeyExchangeModesExtension{Modes: []uint8{1}},
			&utls.SupportedVersionsExtension{Versions: []uint16{0x0304, 0x0303}},
		},
	}
}

func orderedHeaders(raw string) [][2]string {
	out := [][2]string{}
	dec := json.NewDecoder(strings.NewReader(raw))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return out
	}
	for dec.More() {
		kt, err := dec.Token()
		if err != nil {
			break
		}
		var v string
		_ = dec.Decode(&v)
		out = append(out, [2]string{fmt.Sprint(kt), v})
	}
	return out
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: tls_http <url>")
		os.Exit(2)
	}
	// Accept both `tls_http <url>` and the Bun-helper shape `tls_http <ignored.js> <url>`.
	u, err := url.Parse(os.Args[len(os.Args)-1])
	if err != nil {
		panic(err)
	}
	body, _ := io.ReadAll(os.Stdin)
	host := u.Hostname()
	port := u.Port()
	if port == "" {
		port = "443"
	}
	path := u.RequestURI()
	if path == "" {
		path = "/"
	}

	conn, err := net.Dial("tcp", net.JoinHostPort(host, port))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer conn.Close()

	uc := utls.UClient(conn, &utls.Config{
		ServerName:         host,
		InsecureSkipVerify: true,
	}, utls.HelloCustom)
	if err := uc.ApplyPreset(bunSpec()); err != nil {
		fmt.Fprintln(os.Stderr, "preset:", err)
		os.Exit(1)
	}
	if err := uc.Handshake(); err != nil {
		fmt.Fprintln(os.Stderr, "handshake:", err)
		os.Exit(1)
	}

	var req strings.Builder
	fmt.Fprintf(&req, "POST %s HTTP/1.1\r\nHost: %s\r\n", path, u.Host)
	for _, kv := range orderedHeaders(os.Getenv("O2A_HEADERS_JSON")) {
		fmt.Fprintf(&req, "%s: %s\r\n", kv[0], kv[1])
	}
	fmt.Fprintf(&req, "Accept-Encoding: gzip, deflate, br, zstd\r\nConnection: close\r\nContent-Length: %s\r\n\r\n", strconv.Itoa(len(body)))
	if _, err := uc.Write([]byte(req.String())); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if _, err := uc.Write(body); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	br := bufio.NewReader(uc)
	status, _ := br.ReadString('\n')
	code := strings.Fields(strings.TrimPrefix(status, "HTTP/1.1 "))
	if len(code) == 0 {
		fmt.Fprintln(os.Stderr, "empty status line")
		return
	}
	fmt.Print("__O2A_STATUS__ " + code[0] + "\n")
	respHeaders := map[string]string{}
	for {
		line, err := br.ReadString('\n')
		if err != nil || line == "\r\n" || line == "\n" {
			break
		}
		if i := strings.Index(line, ":"); i > 0 {
			respHeaders[strings.ToLower(strings.TrimSpace(line[:i]))] = strings.TrimSpace(line[i+1:])
		}
	}
	hj, _ := json.Marshal(respHeaders)
	fmt.Print("__O2A_HEADERS__ " + string(hj) + "\n")
	if strings.Contains(strings.ToLower(respHeaders["transfer-encoding"]), "chunked") {
		for {
			line, err := br.ReadString('\n')
			if err != nil {
				break
			}
			n, err := strconv.ParseUint(strings.TrimSpace(line), 16, 64)
			if err != nil || n == 0 {
				break
			}
			buf := make([]byte, n)
			if _, err := io.ReadFull(br, buf); err != nil {
				break
			}
			_, _ = os.Stdout.Write(buf)
			_, _ = br.ReadString('\n')
		}
		return
	}
	_, _ = io.Copy(os.Stdout, br)
}
