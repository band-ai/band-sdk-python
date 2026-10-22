defmodule ContractLab.UTF8TextTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, Wire}

  defp compile(type, encoding \\ nil) do
    explicit = if is_nil(encoding), do: "", else: "encoding: #{encoding}\n"

    bytes =
      """
      ---
      name: utf8-text
      description: One bounded UTF-8 value.
      engine: 0.2.0-lab.1
      roles: [sender, receiver]
      """ <>
        explicit <>
        """
        ---
        type value
          text: #{type}
        state new
          sender -> receiver: value() => complete
        end complete
        end expired
        """

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp admission(c, text) do
    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        bindings: %{"sender" => "a", "receiver" => "b"},
        now: 0,
        inactivity: :accepted,
        max_messages: 2,
        room_policy: :unchecked
      )

    {decision, _} =
      Engine.step(
        s,
        %{"id" => "v", "session" => "s", "kind" => "value", "payload" => %{"text" => text}},
        actor: "a",
        now: 0
      )

    decision
  end

  test "a plain number is a mandatory UTF-8 byte maximum, not a grapheme count" do
    assert {:ok, c} = compile("text(4)")
    assert c.card.encoding == "utf-8"

    assert %{kind: :text, encoding: "utf-8", bytes: %{min: 0, max: 4}, graphemes: nil} =
             c.types["value"].fields["text"]

    for v <- ["", "abcd", "éé", "🚀"] do
      assert admission(c, v).status == :accepted, inspect(v)
    end

    for v <- ["abcde", "ééa", "🚀a", "e" <> String.duplicate("\u0301", 10)] do
      assert admission(c, v).reason == :payload, inspect(v)
    end
  end

  test "optional grapheme constraints apply in addition to bytes" do
    assert {:ok, c} = compile("text(bytes: 16, graphemes: 1)")

    for v <- ["e\u0301", "🚀", "🇫🇷"] do
      assert admission(c, v).status == :accepted, inspect(v)
    end

    assert admission(c, "ab").reason == :payload
    assert admission(c, "e" <> String.duplicate("\u0301", 10)).reason == :payload
  end

  test "ranges allow minimum sizes and both limits remain visible to queries" do
    assert {:ok, c} = compile("text(1..8, graphemes: 1..2)")
    assert admission(c, "").reason == :payload
    assert admission(c, "éé").status == :accepted
    assert admission(c, "abc").reason == :payload
    assert {:ok, role} = ContractLab.for_role(c, "sender")
    assert role.types["value"].fields["text"].bytes == %{min: 1, max: 8}
    assert role.types["value"].fields["text"].graphemes == %{min: 1, max: 2}
  end

  test "valid UTF-8 is required, normalization is not performed" do
    assert {:ok, c} = compile("text(2, graphemes: 1)")
    assert admission(c, "é").status == :accepted
    assert admission(c, "e\u0301").reason == :payload

    for invalid <- [<<255>>, <<192, 128>>, <<237, 160, 128>>, <<240, 159, 154>>] do
      assert admission(c, invalid).reason == :payload
    end
  end

  test "JSON escape spelling does not change the field's UTF-8 byte count" do
    assert {:ok, c} = compile("text(4, graphemes: 1)")

    for wire <- [
          ~S({"id":"v","session":"s","kind":"value","payload":{"text":"\ud83d\ude80"}}),
          ~S({"id":"v","session":"s","kind":"value","payload":{"text":"🚀"}})
        ] do
      assert {:ok, message} = Wire.decode(wire)
      assert message["payload"]["text"] == "🚀"
      assert admission(c, message["payload"]["text"]).status == :accepted
    end
  end

  test "encoding is reserved but only utf-8 is supported" do
    assert {:ok, implicit} = compile("text(4)")
    assert {:ok, explicit} = compile("text(4)", "utf-8")
    assert implicit.digest == explicit.digest
    refute implicit.source_digest == explicit.source_digest

    for encoding <- ["utf-16", "latin-1", "UTF-8", "utf8"] do
      assert {:error, %{code: :field_value, line: 6, detail: "encoding"}} =
               compile("text(4)", encoding)
    end
  end

  test "unbounded, grapheme-only, reversed and overflowing bounds are rejected" do
    for type <- [
          "text()",
          "text(graphemes: 5)",
          "text(bytes: infinite)",
          "text(5..1)",
          "text(5, graphemes: 3..1)",
          "text(9007199254740992)",
          "text(5, graphemes: 9007199254740992)",
          "text(5, encoding: utf-16)"
        ] do
      assert {:error, %{code: :type}} = compile(type), type
    end

    assert {:ok, empty} = compile("text(0)")
    assert admission(empty, "").status == :accepted
    assert admission(empty, "a").reason == :payload
  end

  test "frontmatter and standard package headers recognize only the reserved encoding" do
    fields = """
    name: encoding-header
    description: Encoding declaration.
    engine: 0.2.0-lab.1
    roles: [sender, receiver]
    """

    for encoding <- ["utf-8", "utf-16"] do
      frontmatter = "---\n" <> fields <> "encoding: #{encoding}\n---\n"
      result = HeaderSpikes.discover_bytes(:frontmatter, frontmatter)

      if encoding == "utf-8",
        do: assert(match?({:ok, %{card: %{encoding: "utf-8"}}}, result)),
        else: assert(result == {:error, :unsupported_encoding})

      standard = """
      ---
      name: encoding-header
      description: Encoding declaration.
      metadata:
        band-engine: "0.2.0-lab.1"
        band-encoding: "#{encoding}"
        band-roles: "sender,receiver"
        band-contract: "protocol.band"
        band-contract-sha256: "#{Canonical.hash("")}"
      ---
      """

      result = HeaderSpikes.discover_bytes(:standard, standard, package_name: "encoding-header")

      if encoding == "utf-8",
        do: assert(match?({:ok, %{card: %{encoding: "utf-8"}}}, result)),
        else: assert(result == {:error, :unsupported_encoding})
    end
  end

  test "Unicode 16 grapheme segmentation is stable across host runtime versions" do
    assert {:ok, c} = compile("text(100, graphemes: 1)")
    assert admission(c, "क्क").status == :accepted
    # U+1ACF becomes Extend in Unicode 17, but remains separate in this profile.
    assert admission(c, "a\u1ACF").reason == :payload
  end
end
