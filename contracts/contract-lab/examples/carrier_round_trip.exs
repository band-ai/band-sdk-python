# Run from contract-lab with: mix run examples/carrier_round_trip.exs
# These are typed edge values, not an HTTP server, email parser or authentication implementation.
defmodule CarrierRoundTrip do
  alias ContractLab.{Canonical, Engine, Wire}

  def run do
    root = Path.expand("../..", __DIR__)
    carriers = JSON.decode!(File.read!(Path.join(root, "spec/examples/carriers.json")))

    {:ok, contract} =
      ContractLab.load(Path.join(root, "contract-lab/contracts/v2/one-review.band"), :frontmatter)

    # Generate once, then retain this exact ID/envelope for every carrier and retry.
    id = "<#{Base.encode16(:crypto.strong_rand_bytes(16), case: :lower)}@example.invalid>"

    message = %{
      "id" => id,
      "session" => "22222222-2222-4222-8222-222222222222",
      "kind" => "request",
      "payload" => %{"text" => "Review this change."},
      "expected_version" => 0
    }

    values = %{
      "path:id" => message["session"],
      "path:kind" => message["kind"],
      "header:X-Band-Expected-Version" => 0,
      "header:Message-ID" => id,
      "header:X-Band-Session" => message["session"]
    }

    frames =
      Enum.map(carriers, fn carrier ->
        sources = Enum.reject(Map.values(carrier["fields"]), &(&1 == "body"))
        transport = Map.take(values, sources)
        opts = [carrier: carrier, transport: transport]
        {:ok, bytes} = Wire.encode(message, opts)
        {:ok, ^message} = Wire.decode(bytes, opts)
        body = JSON.decode!(bytes)
        omitted = for {field, source} <- carrier["fields"], source != "body", do: field
        true = Enum.all?(omitted, &(not Map.has_key?(body, &1)))
        true = body["payload"] == message["payload"]

        # Duplication is permitted only when the typed values match exactly.
        {:ok, ^message} = Wire.decode(Canonical.encode(message), opts)

        Enum.each(omitted, fn field ->
          wrong = if field == "expected_version", do: 1, else: "different"
          conflicting = Map.put(message, field, wrong)

          {:error, :transport_conflict} =
            Wire.decode(Canonical.encode(conflicting), opts)

          {:error, :transport_conflict} = Wire.encode(conflicting, opts)
        end)

        %{
          name: carrier["name"],
          body: body,
          carrier: carrier,
          transport: transport,
          normalized: message
        }
      end)

    [band, http, email] = frames
    # Declaring a source does not remove a body field when no value was supplied.
    {:ok, full_body} = Wire.encode(message, carrier: band.carrier, transport: %{})
    true = JSON.decode!(full_body) == message
    {:ok, ^message} = Wire.decode(full_body, carrier: band.carrier, transport: %{})

    # Omitted map entries default to body; resolved deployment metadata retains all four fields.
    short_band = put_in(band.carrier, ["fields"], %{"session" => "path:id"})

    {:ok, ^message} =
      Wire.decode(Canonical.encode(band.body), carrier: short_band, transport: band.transport)

    # A declared carrier kind is still checked by the contract, not admitted by its location.
    email_kind = put_in(email.carrier, ["fields", "kind"], "header:X-Band-Kind")
    kind_values = Map.put(email.transport, "header:X-Band-Kind", "request")
    kind_opts = [carrier: email_kind, transport: kind_values]
    {:ok, kind_bytes} = Wire.encode(message, kind_opts)
    {:ok, ^message} = Wire.decode(kind_bytes, kind_opts)

    {:error, :envelope} =
      Wire.decode(Canonical.encode(Map.delete(email.body, "kind")),
        carrier: email.carrier,
        transport: email.transport
      )

    {:error, :envelope} =
      Wire.decode(Canonical.encode(%{"kind" => "request", "payload" => %{}}),
        carrier: email.carrier,
        transport: %{}
      )

    {:error, :envelope} = Wire.decode(Canonical.encode(Map.put(message, "actor", "claimed")))

    {:error, :transport_fields} =
      Wire.decode(Canonical.encode(email.body),
        carrier: email.carrier,
        transport: Map.put(email.transport, "header:From", "claimed")
      )

    {:error, :transport_fields} =
      Wire.decode(Canonical.encode(message),
        carrier: put_in(band.carrier, ["fields", "actor"], "header:From")
      )

    {:error, :transport_fields} =
      Wire.decode(Canonical.encode(message),
        carrier: put_in(band.carrier, ["fields", "payload"], "header:Payload")
      )

    {:error, :transport_fields} =
      Wire.decode(Canonical.encode(message),
        carrier: %{band.carrier | "profile" => "band-contract/carrier/999"}
      )

    {:error, :envelope} =
      Wire.decode(Canonical.encode(http.body),
        carrier: http.carrier,
        transport: Map.put(http.transport, "header:X-Band-Expected-Version", "0")
      )

    lead = "Agent:44444444-4444-4444-8444-444444444444"
    reviewer = "Agent:55555555-5555-4555-8555-555555555555"
    bindings = %{"lead" => lead, "reviewer" => reviewer}

    policy = %{
      revision: "local-example/1",
      principals:
        Map.new([lead, reviewer], &{&1, %{kind: :agent, active: true, permission: "participant"}}),
      fixed_roles: bindings
    }

    {:ok, initial} =
      Engine.new(contract,
        session_id: message["session"],
        bindings: bindings,
        now: 0,
        max_messages: 10,
        room_policy: policy,
        # The example is the full reference host: it states every extension the contract records.
        capabilities: contract.card.extensions
      )

    # This fixture actor is trusted test context. No field/header selects it.
    {accepted, after_acceptance} =
      Engine.step(initial, band.normalized, actor: lead, now: 0, room_policy: policy)

    :accepted = accepted.status

    {duplicate, after_retry} =
      Engine.step(after_acceptance, email.normalized, actor: lead, now: 0, room_policy: policy)

    :duplicate = duplicate.status
    ^accepted = duplicate.original
    true = after_retry == after_acceptance

    {unauthorized, ^initial} =
      Engine.step(initial, email.normalized, actor: "unbound", now: 0, room_policy: policy)

    :unauthorized = unauthorized.reason

    {:ok, unknown_kind} =
      Wire.decode(Canonical.encode(Map.delete(email.body, "kind")),
        carrier: email_kind,
        transport: Map.put(kind_values, "header:X-Band-Kind", "not-declared")
      )

    {rejected_kind, _} =
      Engine.step(initial, unknown_kind, actor: lead, now: 0, room_policy: policy)

    :rejected = rejected_kind.status

    IO.puts(
      Canonical.encode(%{
        "passed" => true,
        "profile" => "band-contract/carrier/1",
        "contract" => %{
          "engine" => contract.engine,
          "source" => contract.source_digest,
          "definition" => contract.digest
        },
        "frames" => Enum.map(frames, &Map.take(&1, [:name, :body, :transport, :normalized])),
        "checks" =>
          ~w(band-roundtrip http-roundtrip email-roundtrip omission matching-duplication decode-conflicts encode-conflicts unsupplied-source body-defaults declared-email-kind missing-kind missing-id body-actor undeclared-source actor-map payload-map invalid-profile typed-version cross-carrier-duplicate auth-only-actor contract-kind-validation),
        "qualification" =>
          "local normalization and reducer only; no live HTTP/email/Band authentication or deployment"
      })
    )
  end
end

CarrierRoundTrip.run()
